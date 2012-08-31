"""
CSV representers.

This file is part of the everest project. 
See LICENSE.txt for licensing, CONTRIBUTORS.txt for contributor information.

Created on May 19, 2011.
"""
from __future__ import absolute_import # Makes the import below absolute
from collections import OrderedDict
from csv import Dialect
from csv import QUOTE_NONNUMERIC
from csv import reader
from csv import register_dialect
from csv import writer
from everest.mime import CsvMime
from everest.representers.base import RepresentationGenerator
from everest.representers.base import RepresentationParser
from everest.representers.base import ResourceRepresenter
from everest.representers.config import RepresenterConfiguration
from everest.representers.converters import BooleanConverter
from everest.representers.converters import ConverterRegistry
from everest.representers.converters import DateTimeConverter
from everest.representers.converters import NoOpConverter
from everest.representers.dataelements import SimpleCollectionDataElement
from everest.representers.dataelements import SimpleLinkedDataElement
from everest.representers.dataelements import SimpleMemberDataElement
from everest.representers.mapping import SimpleMappingRegistry
from everest.representers.traversal import DataElementTreeTraverser
from everest.representers.traversal import PROCESSING_DIRECTIONS
from everest.representers.traversal import ResourceDataVisitor
from everest.representers.utils import get_mapping_registry
from everest.resources.utils import get_member_class
from everest.resources.utils import is_resource_url
from everest.resources.utils import provides_member_resource
from itertools import product
import datetime
from everest.representers.interfaces import IRepresentationConverter
from zope.interface import classProvides as class_provides # pylint: disable=E0611,F0401

__docformat__ = 'reStructuredText en'
__all__ = ['CsvCollectionDataElement',
           'CsvData',
           'CsvDataElementTreeVisitor',
           'CsvLinkedDataElement',
           'CsvMappingRegistry',
           'CsvMemberDataElement',
           'CsvRepresentationGenerator',
           'CsvRepresentationParser',
           'CsvRepresenterConfiguration',
           'CsvResourceRepresenter',
           ]


class _DefaultCsvDialect(Dialect): # ignore no __init__ pylint: disable=W0232
    """
    Default dialect to use when exporting resources to CSV.
    """
    delimiter = ','
    quotechar = '"'
    doublequote = True
    skipinitialspace = False
    lineterminator = '\n'
    quoting = QUOTE_NONNUMERIC
register_dialect('export', _DefaultCsvDialect)
register_dialect('import', _DefaultCsvDialect)


class CsvConverterRegistry(ConverterRegistry):
    pass


class CsvIntConverter(object):
    """
    Specialized converter coping with the CSV reader's unfortunate habit
    to convert integers to floats upon reading.
    """
    class_provides(IRepresentationConverter)

    @classmethod
    def from_representation(cls, value):
        if isinstance(value, float):
            value = int(value)
        return value

    @classmethod
    def to_representation(cls, value):
        return value

CsvConverterRegistry.register(datetime.datetime, DateTimeConverter)
CsvConverterRegistry.register(bool, BooleanConverter)
CsvConverterRegistry.register(int, CsvIntConverter)
CsvConverterRegistry.register(float, NoOpConverter)


class CsvRepresentationParser(RepresentationParser):
    """
    Parser converting CSV representations of resources into a data element.
    
    :note: Nested resources have to be provided as links (i.e., there is no 
           support for recursive data element tree building).
    """

    def run(self):
        mp_reg = get_mapping_registry(CsvMime)
        is_member_rpr = provides_member_resource(self._resource_class)
        if is_member_rpr:
            member_cls = self._resource_class
            result_data_el = None
        else:
            # Collection resource: Create a wrapping collection data element.
            member_cls = get_member_class(self._resource_class)
            coll_mp = mp_reg.find_or_create_mapping(self._resource_class)
            coll_data_el = coll_mp.create_data_element()
            result_data_el = coll_data_el
        mb_mp = mp_reg.find_or_create_mapping(member_cls)
        csv_reader = reader(self._stream, self.get_option('dialect'))
        attrs = mb_mp.get_attribute_map()
        header = None
        for row in csv_reader:
            mb_data_el = mb_mp.create_data_element()
            if header is None:
                # Check if the header is valid.
                attr_names = attrs.keys()
                header = row
                for attr in header:
                    if not attr in attr_names:
                        raise ValueError('Invalid field "%s" in CSV input '
                                         'detected.' % attr)
                continue
            if len(row) != len(header):
                raise ValueError("Invalid row length (found: %s, expected: "
                                 "%s)." % (len(row), len(header)))
            for csv_attr, value in zip(header, row):
                if value == '':
                    value = None
                attr = attrs[csv_attr]
                if is_resource_url(value):
                    link = CsvLinkedDataElement.create(value, attr.kind)
                    mb_data_el.set_nested(attr, link)
                else:
                    mb_data_el.set_terminal_converted(attr, value)
            if is_member_rpr:
                result_data_el = mb_data_el
            else:
                coll_data_el.add_member(mb_data_el)
        return result_data_el


class CsvData(object):
    def __init__(self, data=None):
        if data is None:
            data = {}
        self.fields = []
        self.data = []
        for attr_name, value in data.iteritems():
            if not isinstance(value, CsvData):
                self.fields.append(attr_name)
                if len(self.data) == 0:
                    self.data.append([value])
                else:
                    for row in self.data:
                        row.append(value)
            else:
                self.expand(value)

    def expand(self, other):
        if len(self.data) == 0:
            self.data = other.data
        else:
            new_data = []
            for self_row, other_row in list(product(self.data, other.data)):
                new_data.append(self_row + other_row)
            self.data = new_data
        self.fields = self.fields + other.fields

    def append(self, other):
        if len(self.data) == 0:
            self.data = other.data
            self.fields = other.fields
        else:
            for row in other.data:
                self.data.append(row)

    def __len__(self):
        return len(self.data)


class CsvDataElementTreeVisitor(ResourceDataVisitor):
    def __init__(self, encoding):
        ResourceDataVisitor.__init__(self)
        self.__encoding = encoding
        self.__csv_data = None

    def visit_member(self, attribute_key, attribute, member_node, member_data,
                     is_link_node, parent_data, index=None):
        if is_link_node:
            new_field_name = self.__get_field_name(attribute_key[:-1],
                                                   attribute)
            mb_data = CsvData({new_field_name:
                               self.__encode(member_node.get_url())})
        else:
            rpr_mb_data = OrderedDict()
            for attr, value in member_data.iteritems():
                new_field_name = self.__get_field_name(attribute_key, attr)
                rpr_mb_data[new_field_name] = value
            mb_data = CsvData(rpr_mb_data)
        if not index is None:
            # Collection member. Store in parent data with index as key.
            parent_data[index] = mb_data
        elif len(attribute_key) == 0:
            # Top level - store as CSV data..
            self.__csv_data = mb_data
        else:
            # Nested member. Store in parent data with attribute as key.
            parent_data[attribute] = mb_data

    def visit_collection(self, attribute_key, attribute, collection_node,
                         collection_data, is_link_node, parent_data):
        if is_link_node:
            new_field_name = self.__get_field_name(attribute_key[:-1],
                                                   attribute)
            coll_data = CsvData({new_field_name:collection_node.get_url()})
        else:
            coll_data = CsvData()
            for item in sorted(collection_data.items()):
                mb_data = item[1]
                coll_data.append(mb_data)
        if len(attribute_key) == 0:
            self.__csv_data = coll_data
        else:
            parent_data[attribute] = coll_data

    @property
    def csv_data(self):
        return self.__csv_data

    def __get_field_name(self, attribute_key, attribute):
        if attribute.name != attribute.repr_name:
            field_name = attribute.repr_name
        else:
            field_name = '.'.join(attribute_key + (attribute.name,))
        return self.__encode(field_name)

    def __encode(self, item):
        if isinstance(item, unicode):
            item = item.encode(self.__encoding)
        return item


class CsvRepresentationGenerator(RepresentationGenerator):
    """
    A generator converting data elements into CSV representations.

    :note: Nested member and collection resources are handled by adding 
           more columns (member attributes) and rows (collection members)
           dynamically. By default, column names for nested member attributes
           are built as dot-concatenation of the corresponding attribute key.
    """

    def run(self, data_element):
        # We do not want the traverser to emit converted terminals since the
        # CSV writer will take care of that.
        trv = DataElementTreeTraverser(data_element, self._mapping,
                                       PROCESSING_DIRECTIONS.WRITE)
#                                       convert_terminals=False)
        vst = CsvDataElementTreeVisitor(self.get_option('encoding'))
        trv.run(vst)
        csv_data = vst.csv_data
        if len(csv_data) > 0:
            csv_writer = writer(self._stream,
                                dialect=self.get_option('dialect'))
            csv_writer.writerow(csv_data.fields)
            for row_data in csv_data.data:
                csv_writer.writerow(row_data)


class CsvResourceRepresenter(ResourceRepresenter):

    content_type = CsvMime

    #: The CSV dialect to use for exporting CSV data.
    CSV_EXPORT_DIALECT = 'export'
    #: The CSV dialect to use for importing CSV data.
    CSV_IMPORT_DIALECT = 'import'
    #: The encoding to use for exporting and importing CSV data.
    ENCODING = 'utf-8'

    @classmethod
    def make_mapping_registry(cls):
        return CsvMappingRegistry()

    def _make_representation_parser(self, stream, resource_class, mapping):
        parser = CsvRepresentationParser(stream, resource_class, mapping)
        parser.set_option('dialect', self.CSV_IMPORT_DIALECT)
        return parser

    def _make_representation_generator(self, stream, resource_class, mapping):
        generator = CsvRepresentationGenerator(stream, resource_class, mapping)
        generator.set_option('dialect', self.CSV_EXPORT_DIALECT)
        generator.set_option('encoding', self.ENCODING)
        return generator


class CsvMemberDataElement(SimpleMemberDataElement):
    converter_registry = CsvConverterRegistry


class CsvCollectionDataElement(SimpleCollectionDataElement):
    pass


class CsvLinkedDataElement(SimpleLinkedDataElement):
    pass


class CsvRepresenterConfiguration(RepresenterConfiguration):
    pass


class CsvMappingRegistry(SimpleMappingRegistry):
    """
    Registry for CSV mappings.
    """
    member_data_element_base_class = CsvMemberDataElement
    collection_data_element_base_class = CsvCollectionDataElement
    linked_data_element_base_class = CsvLinkedDataElement
    configuration_class = CsvRepresenterConfiguration
