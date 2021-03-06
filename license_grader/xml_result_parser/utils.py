from __future__ import unicode_literals
from django.shortcuts import render
from xml.dom.minidom import parse, parseString
import xml.dom.minidom
from xmlbuilder import XMLBuilder
from subprocess import call
from fabric.api import env, local, task, warn_only
from lxml import etree
from colorama import Fore, Back, Style
from colorama import init, deinit
init()

DEFAULT_CLOC_COMMAND_RESULT = """
    <results>
        <header>
          <cloc_url>http://cloc.sourceforge.net</cloc_url>
          <cloc_version>1.60</cloc_version>
          <elapsed_seconds>0</elapsed_seconds>
          <n_files>0</n_files>
          <n_lines>0</n_lines>
          <files_per_second>0</files_per_second>
          <lines_per_second>0</lines_per_second>
        </header>
        <files>
        <file name="no/name" blank="0" comment="0" code="0"  language="Python" />
        <total blank="0" comment="0" code="0" />
    </files>
</results>"""
THRESHOLD_VALUE = 80
VALUES_TO_AVOID = ['NOASSERTION', 'NONE', 'Match']
MSG = {True: Fore.BLUE + 'Good! The spdx document and the source files match.',
       False: Fore.RED + 'Could not proceed because the source files do not match with the spdx document provided.'}
SCALE = [('A', 90, Fore.GREEN), ('B', 75, Fore.BLUE), ('C', 55,
         Fore.MAGENTA), ('D', 30, Fore.YELLOW)]

class ScanSpdx:
    spdx_file = None
    scan_results = None


    def __init__(self, spdx_file):
        self.spdx_file = spdx_file


    def scan(self):
        # call(["python -s spdx_scanner.py -s 10571", self.spdx_file])
        spdx_file = self.spdx_file
        spdx_scan_result = local('python -s spdx_scanner.py -s 10571 {spdx_file}'.format(spdx_file=spdx_file), capture=True)
        self.scan_results = XMLBuilder('spdx_file')
        with self.scan_results.data:
            for line in spdx_scan_result.splitlines():
                single_line = line.split(',')
                with self.scan_results.item:
                    self.scan_results.file(val=single_line[0])
                    self.scan_results.license_info(val=single_line[1])
                    self.scan_results.license_concluded(val=single_line[2])
                etree_node = ~self.scan_results
        print(str(self.scan_results))
        return str(self.scan_results)


class AnalysePackage:
    source_package = None
    min_code_line = 0
    analysis_results = None


    def __init__(self, source_package, min_code_lines):
        self.source_package = source_package
        self.min_code_lines = min_code_lines


    def analyse(self):
        package = self.source_package
        pkg_scan_result = local('cloc --xml --force-lang="PHP",in --force-lang="PHP",conf --force-lang="PHP",twig --force-lang="PHP",json --by-file {package}'.format(package=package), capture=True)
        if len(pkg_scan_result.split('\n')) < 4:
            self.analysis_results = DEFAULT_CLOC_COMMAND_RESULT
        else:
            self.analysis_results = pkg_scan_result.split("\n",4)[4]
        valid_code_lines = self.validate(etree.tostring(etree.fromstring(self.analysis_results)), self.min_code_lines)
        print(pkg_scan_result)
        return [pkg_scan_result, valid_code_lines[1]]


    def validate(self, xml_results, min_code_lines):
        DOMTree = xml.dom.minidom.parseString(xml_results)
        collection = DOMTree.documentElement
        num_source_file = 0
        for file_ in collection.getElementsByTagName('file'):
            if file_.hasAttribute('code'):
                attribute_value = file_.getAttribute('code')
                if int(attribute_value) >= int(min_code_lines):
                    num_source_file += 1
        return [xml_results, num_source_file]


class CheckPackage:
    source_package = None
    spdx_file = None
    min_code_lines = 0
    spdx_scan_results = None
    package_analysis_results = None
    spdx_scan_results_root = None
    package_analysis_results_root = None
    source_collection = None
    spdx_collection = None

    def __init__(self, spdx_file, source_package, min_code_lines):
        self.source_package = source_package
        self.spdx_file = spdx_file
        self.min_code_lines = min_code_lines


    def check(self):
        scan_obj = ScanSpdx(self.spdx_file)
        analysis_obj = AnalysePackage(self.source_package, self.min_code_lines)
        self.package_analysis_results = analysis_obj.analyse()
        self.spdx_scan_results = scan_obj.scan()
        self.spdx_scan_results_root = etree.fromstring(self.spdx_scan_results)
        formatted_package_analysis_result = ""
        if len(self.package_analysis_results[0].split('\n')) < 4:
            formatted_package_analysis_result = DEFAULT_CLOC_COMMAND_RESULT
        else:
            formatted_package_analysis_result = self.package_analysis_results[0].split("\n",4)[4]
        self.package_analysis_results_root = etree.fromstring(formatted_package_analysis_result)
        grade = self.establish_link()
        is_valid = grade >= THRESHOLD_VALUE
        print('The package matches the spdx file by {0}, the lowest permitted value is: {1}'.format(grade, THRESHOLD_VALUE))
        return [is_valid, self.spdx_scan_results_root, self.package_analysis_results_root, self.package_analysis_results[1]]

    def establish_link(self):
        results_dict = {'total_number_of_files': 0, 'num_common_files': 0}
        spdx_scan_results = etree.tostring(self.spdx_scan_results_root)
        source_package_results = etree.tostring(self.package_analysis_results_root)

        spdxDOMTree = xml.dom.minidom.parseString(spdx_scan_results)
        sourceDOMTree = xml.dom.minidom.parseString(source_package_results)
        self.spdx_collection = spdxDOMTree.documentElement
        self.source_collection = sourceDOMTree.documentElement

        # Get detail of each useful attribute.
        results_dict['num_common_files'] = \
            self.get_number_of_common_files()
        results_dict['total_number_of_files'] = \
            self.get_xml_item_value('n_files')
        grade = "0 %"
        if results_dict['total_number_of_files'] != '0':
            grade = 100 * (float(results_dict['num_common_files'])
                           / float(results_dict['total_number_of_files']))
        return grade


    def get_number_of_common_files(self):
        item_count = 0
        spdx_scan_results = etree.tostring(self.spdx_scan_results_root)
        source_package_results = etree.tostring(self.package_analysis_results_root)

        for item_ in self.source_collection.getElementsByTagName('file'):
            if item_.hasAttribute('name'):
                attribute_value = item_.getAttribute('name')

                file_link = '/'.join(attribute_value.split('/')[-2:])
                if file_link in spdx_scan_results:
                    item_count += 1

        return item_count


    def get_xml_item_value(self, tag_to_get):
        attribute_value = 0
        elt_list = self.source_collection.getElementsByTagName(tag_to_get)
        if len(elt_list):
            attribute_value = elt_list[0].firstChild.nodeValue
        return attribute_value


class GradePackage:
    spdx_scan_results = None
    package_analysis_results = None
    spdx_file = None
    package = None
    min_code_lines = None
    packageCollection = None
    spdxCollection = None
    spdxDOMTree = None
    results_dict = {
        'num_license_concluded': 0,
        'num_license_possible': 0,
        'total_num_source_files': 0,
        'total_num_files_with_license': 0,
        }
    check_results = None
    xml_results = None

    def __init__(self, spdx_file, package, min_code_lines):
        self.spdx_file = spdx_file
        self.package = package
        self.min_code_lines = min_code_lines

    def grade(self):
        check_obj = CheckPackage(self.spdx_file, self.package, self.min_code_lines)
        self.check_results = check_obj.check()
        print(MSG[self.check_results[0]])
        if self.check_results[0]:
            self.spdx_scan_results = self.check_results[1]
            self.package_analysis_results = self.check_results[2]
            self.xml_results = self.parse_xml_results()

    def parse_xml_results(self):
        spdx_results = etree.tostring(self.spdx_scan_results)
        package_analysis_results = etree.tostring(self.package_analysis_results)

        self.spdxDOMTree = xml.dom.minidom.parseString(spdx_results)
        self.spdxCollection = self.spdxDOMTree.documentElement
        packageDOMTree = xml.dom.minidom.parseString(package_analysis_results)
        self.packageCollection = packageDOMTree.documentElement

        self.results_dict['num_license_concluded'] = self.get_xml_item_count('license_concluded', VALUES_TO_AVOID, 'val')
        self.results_dict['num_license_possible'] =  self.get_xml_item_count('license_info', VALUES_TO_AVOID, 'val')
        self.results_dict['total_num_source_files'] = self.check_results[3]
        self.results_dict['total_num_files_with_license'] = self.get_xml_item_count('license_concluded', VALUES_TO_AVOID, 'val')
        return self.compute_grade()


    def compute_grade(self):
        grade1 = "0 %"
        grade2 = "0 %"
        if self.results_dict['total_num_source_files'] > 0:
            grade1 = 100 * (float(self.results_dict['num_license_possible'
                            ]) / float(self.results_dict['total_num_source_files'
                            ]))
            grade2 = 100 * (float(self.results_dict['num_license_concluded'])
                            / float(self.results_dict['total_num_source_files']))
        return (self.grade_scale(grade2, 2), self.grade_scale(grade1, 1))


    def grade_scale(self, grade_num, gtype):
        if grade_num > SCALE[0][1]:
            return self.grade_string(SCALE[0][0], grade_num, gtype)
        if grade_num > SCALE[1][1]:
            return self.grade_string(SCALE[1][0], grade_num, gtype)
        if grade_num > SCALE[2][1]:
            return self.grade_string(SCALE[2][0], grade_num, gtype)
        if grade_num > SCALE[3][1]:
            return self.grade_string(SCALE[3][0], grade_num, gtype)
        else:
            return self.grade_string('F', grade_num, gtype)


    def grade_string(self, grade, grade_num, gtype):
        """Dispays the grade with a color following its value; Red for F, Green for A, etc"""
        additional_info = ''
        if grade_num > SCALE[0][1]:
            color = SCALE[0][2]
        elif grade_num > SCALE[1][1]:
            color = SCALE[1][2]
        elif grade_num > SCALE[2][1]:
            color = SCALE[2][2]
        elif grade_num > SCALE[3][1]:
            color = SCALE[3][2]
        else:
            color = Fore.RED
        if gtype == 1:
            additional_info = 'files_with_any_kind_of_license_infos'
        if gtype == 2:
            additional_info = 'files_with_license_concluded'

        print color + '{0} {1} with {2} %  pass for {3}'.format('GRADE: ', grade, grade_num, additional_info)
        deinit()


    def get_xml_item_count(self,
        item,
        values_to_avoid,
        tag_to_get,
        ):
        item_count = 0
        attribute_value = 0
        sub_item_value = ''
        sub_item_name_value = ''
        item_tags = self.spdxCollection.getElementsByTagName('item')
        for item_ in item_tags:
            sub_item = item_.getElementsByTagName(item)
            sub_item_name = item_.getElementsByTagName('file')
            if sub_item[0].hasAttribute(tag_to_get):
                sub_item_value = sub_item[0].getAttribute(tag_to_get)
            if sub_item_name[0].hasAttribute(tag_to_get):
                sub_item_name_value = sub_item_name[0].getAttribute(tag_to_get)
            if self.file_exists(sub_item_name_value):
                if sub_item_value not in values_to_avoid:
                    item_count += 1
        return item_count

    def file_exists(self, filename):
        item_tags = self.packageCollection.getElementsByTagName('file')
        file_existence = False
        for item_ in item_tags:
            if item_.hasAttribute('name'):
                item_value = item_.getAttribute('name')
                if filename in item_value:
                    file_existence = True
        return file_existence
