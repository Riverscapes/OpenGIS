import argparse
import os
from xml.etree import ElementTree as ET

from rscommons import Logger, dotenv, ModelConfig, RSReport, RSProject
from rscommons.util import safe_makedirs
from rscommons.plotting import line

from vbet.__version__ import __version__
from vbet.vbet_database import load_configuration


class VBETReport(RSReport):

    def __init__(self, scenario_id, inputs_database, results_database, report_path, rs_project):
        super().__init__(rs_project, report_path)
        self.log = Logger('VBET Report')
        self.inputs_database = inputs_database
        self.results_database = results_database

        self.images_dir = os.path.join(os.path.dirname(report_path), 'images')
        safe_makedirs(self.images_dir)

        self.vbet_run = load_configuration(scenario_id, inputs_database)

        self.project_root = rs_project.project_dir
        # self.report_intro()

        self.transforms()

    def report_intro(self):
        realization = self.xml_project.XMLBuilder.find('Realizations').find('VBET')

        section_in = self.section('Inputs', 'Inputs')
        inputs = list(realization.find('Inputs'))
        [self.layerprint(lyr, section_in, self.project_root) for lyr in inputs if lyr.tag in ['DEM', 'Raster', 'Vector', 'Geopackage']]

        section_inter = self.section('Intermediates', 'Intermediates')
        intermediates = list(realization.find('Intermediates'))
        [self.layerprint(lyr, section_inter, self.project_root) for lyr in intermediates if lyr.tag in ['DEM', 'Raster', 'Vector', 'Geopackage']]

        section_out = self.section('Outputs', 'Outputs')
        outputs = list(realization.find('Outputs'))
        [self.layerprint(lyr, section_out, self.project_root) for lyr in outputs if lyr.tag in ['DEM', 'Raster', 'Vector', 'Geopackage']]

    def transforms(self):
        section = self.section("Report Intro", "Transforms")

        for name, values in self.vbet_run['Inputs'].items():
            section_input = self.section(None, name, level=2)

            for zone_id, zone_values in enumerate(values['transform_zones'].values()):
                section_zone = self.section("Transforms", f'Drainage Area Zone: {zone_id}', level=3)

                transform_wrapper = ET.Element('div', attrib={'class': 'transformWrapper'})

                table_wrapper = ET.Element('div', attrib={'class': 'tableWrapper'})
                self.create_table_from_dict(zone_values, table_wrapper)
                transform_wrapper.append(table_wrapper)

                x_values = self.vbet_run['Transforms'][name][zone_id].x
                y_values = self.vbet_run['Transforms'][name][zone_id].y

                table_wrapper2 = ET.Element('div', attrib={'class': 'tableWrapper'})
                self.create_table_from_tuple_list([name, 'Normalized Value'], [(x, y) for x, y in zip(x_values, y_values)], table_wrapper2)
                transform_wrapper.append(table_wrapper2)

                image_path = os.path.join(self.images_dir, f'transform_{name}_zone{zone_id}.png')
                line(x_values, y_values, f'{name} value', 'Normalized Evidence', name, image_path)

                img_wrap = ET.Element('div', attrib={'class': 'imgWrap'})
                img = ET.Element('img', attrib={'class': 'line', 'alt': 'line', 'src': f'{os.path.basename(self.images_dir)}/{os.path.basename(image_path)}'})
                img_wrap.append(img)
                transform_wrapper.append(img_wrap)
                section_zone.append(transform_wrapper)


if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument('scenario_code')
    parser.add_argument('input_database')
    parser.add_argument('results_database')
    parser.add_argument('projectxml', help='Path to the VBET project.rs.xml', type=str)
    parser.add_argument('report_path', help='Output path where report will be generated', type=str)
    args = dotenv.parse_args_env(parser)

    cfg = ModelConfig('http://xml.riverscapes.xyz/Projects/XSD/V1/VBET.xsd', __version__)
    project = RSProject(cfg, args.projectxml)
    report = VBETReport(args.scenario_code, args.input_database, args.results_database, args.report_path, project)
    report.write()
