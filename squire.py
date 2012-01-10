#!/usr/local/bin/python

"""Usage: squire [--csv [--output-file-csv=FILENAME]] [--xml [--output-file-xml=FILENAME]] --file=FILENAME

Processes Millennium Title Paging List into structured formats.

Report bugs to <andrew.livesay@gmail.com>.
"""

import sys
import getopt
import re
import operator
import csv
import codecs
from xml.dom.minidom import Document
from cgi import escape
from datetime import datetime

from WebPACScraper import WebPACScraper

###############################################################################
# config
###############################################################################

# WebPAC server hostname
CATALOG_HOSTNAME = "catalog.yourlibrary.org"

# WebPAC server port
CATALOG_PORT = 80

# Special location suffixes (e.g., 'New' in 'Central Media New')
SPECIAL_LOCATION_SUFFIXES = ["New"]

# file containing list of locations (e.g., 'St Johns', 'Central')
LOCATIONS_FILE = "/etc/squired/locations.cfg"

# file containing list of sub locations (e.g., 'AudioBooks' or 'Large Print')
SUBLOCATIONS_FILE = "/etc/squired/sublocations.cfg"

# Use Location/Call Number info scraped from WebPAC instead of data parsed from Title Paging List
FAVOR_WEBPACSCRAPER_DATA = False

# Output 'location' field in CSV?
INCLUDE_LOCATION_IN_CSV = False

# Output 'publishing' field in CSV?
INCLUDE_PUBLISHING_IN_CSV = False

# Output 'pickup_location' field in CSV?
INCLUDE_PICKUP_LOCATION_IN_CSV = False

# Write UTF-8 byte order mark to CSV files?
WRITE_BOM_TO_CSV = True

###############################################################################

class FlagTypes:
    INVALID         = "E"   # Error occurred, probably in WebPACScraper
    CLOSED_STACKS   = "C"   # WebPAC Location contains "CLOSED STACKS"
    OVERSIZED       = "O"   # WebPAC Location contains "OVERSIZE"
    SHORT_STORIES   = "S"   # WebPAC Location contains "SHORT STOR"
    NEW             = "N"   # WebPAC Location ends with "New"

class LineTypes:
    INVALID       = 0
    CAPTION       = 1 #                             Title Paging List
    TIMESTAMP     = 2 #                                                    DAY SHORT_MONTH 01 2011 12:00AM
    BLANK         = 3 #
    BLOCK_LINE_1  = 4 #LOCATION CALL_NUMBER BIB_NUMBER
    BLOCK_LINE_2  = 5 #TITLE/AUTHOR.
    BLOCK_LINE_3  = 6 #PUBLISHER, COPY_YEAR. [or VOLUME]
    BLOCK_LINE_4  = 7 #PICKUP_LOCATION [or PUBLISHER, COPY_YEAR. if VOLUME is present]
    BLOCK_LINE_5  = 8 #[PICKUP_LOCATION if VOLUME is present]
    PAGE_MARK     = 9 #PAGE #

# GLOBAL - scraper
scraper = None

class RegexBorg:
    """Borg for location regex."""
    __shared_state   = {}
    __sublocations   = []
    __locations      = []
    __location_regex = None

    def __init__(self):
        self.__dict__ = self.__shared_state
        if not self.__location_regex:
            self.__load_locations(LOCATIONS_FILE)
            self.__load_sublocations(SUBLOCATIONS_FILE)
            self.get_location_regex()
        
    def __load_sublocations(self, filename):
        try:
            file = open(filename)
            self.__sublocations = [line.strip() for line in file]
            file.close()

        except IOError:
            sys.stderr.write("Error: __load_sublocations: unable to open file: %s\n" % (filename))
            sys.exit(1)

    def __load_locations(self, filename):
        try:
            file = open(filename)
            self.__locations = [line.strip() for line in file]
            file.close()

        except IOError:
            sys.stderr.write("Error: __load_locations: unable to open file: %s\n" % (filename))
            sys.exit(1)

    def get_location_regex(self):
        """Generates an *insane* regex for matching all possible locations in BLOCK_LINE_1."""

        # return cached value if available
        if self.__location_regex:
            return self.__location_regex

        # special prefix matching required for location names containing spaces
        prefixes = [re.escape(loc) for loc in self.__locations if loc.find(" ") != -1]
        prefixes.extend([r"\w+"])

        suffixes = SPECIAL_LOCATION_SUFFIXES

        # start of regex 
        rtv = r"("
        
        v = []
        # WARNING: O(N^2)
        for prefix in prefixes:
            v.extend([(r"%s\ %s" % (prefix, re.escape(loc))) for loc in self.__sublocations])
            for suffix in suffixes:
                v.extend([(r"%s\ %s\ %s" % (prefix, re.escape(loc), suffix)) for loc in self.__sublocations])
        
        # add final branch selectors
        for prefix in prefixes:
            v.append(r"%s" % (prefix))
            for suffix in suffixes:
                v.append(r"%s\ %s" % (prefix, suffix))

        # sort by length
        v.sort(key=len, reverse=True)

        rtv += '|'.join(v)

        # end of regex
        rtv += r")"
        
        # cache value
        self.__location_regex = rtv
        
        return rtv


def load_records(filename, location_filter=None):
    """Loads title paging list text file."""

    records = {}
    last_type = LineTypes.INVALID
    regex_borg = RegexBorg()
    block_range = range(LineTypes.BLOCK_LINE_1, LineTypes.BLOCK_LINE_5 + 1)

    global scraper
    scraper = WebPACScraper(CATALOG_HOSTNAME, CATALOG_PORT)
 
    try:
        file = open(filename)

        try:

            # skip initial blank lines, find "Title Paging List"
            while 1:
                raw_line = file.readline()

                if not raw_line:
                    sys.stderr.write("Error: file does not appear to be a Title Paging List: %s\n" % (filename))
                    sys.exit(1)

                if not raw_line.strip() == "" and raw_line.strip() == "Title Paging List":
                    break

            # first line is CAPTION
            line_type = LineTypes.CAPTION

            record = {}

            while 1:
                raw_line = file.readline()
                if not raw_line:
                    break

                line_type = get_line_type(raw_line, line_type)

                if line_type == LineTypes.INVALID:
                    # TODO: try to recover by skipping to next records block
                    raise ValueError("load_records: invalid line: %s" % (raw_line))

                if line_type in block_range:
                    parse_record_line(record, raw_line, line_type, regex_borg.get_location_regex())

                # done with the block, so add it to records
                if line_type == LineTypes.BLANK and \
                   (last_type == LineTypes.BLOCK_LINE_3 or last_type == LineTypes.BLOCK_LINE_4 or last_type == LineTypes.BLOCK_LINE_5):

                    if last_type == LineTypes.BLOCK_LINE_3:

                        record["pickup_location"] = record["publishing"]
                        record["publishing"] = str(None)

                    # set record flags
                    set_record_flags(record, record["location"], scraper)

                    # update count on duplicates...
                    if not record["bib_number"] in records:
                        record["requested_count"] = 1
                        records[record["bib_number"]] = record

                    # or add new records
                    else:
                        records[record["bib_number"]]["requested_count"] += 1

                    # clear current record
                    record = {}

                # save line_type
                last_type = line_type

        finally:
            file.close()

    except IOError, e:
        sys.stderr.write("Error: load_records: %s\n" % (e))
        sys.exit(1)

    return records


def get_line_type(line=None, last_type=LineTypes.INVALID):
    """Attempts to determine parsed line type."""
    # determine raw_line type
    if line:
    # does it start with white space?
        if re.findall(r"^\s+", line):
        # BLANK, CAPTION, or TIMESTAMP?
            if not line.strip():
                if last_type == LineTypes.BLOCK_LINE_2:
                    return LineTypes.BLOCK_LINE_3
                else:
                    return LineTypes.BLANK
            elif last_type == LineTypes.BLOCK_LINE_1:
                return LineTypes.BLOCK_LINE_2
            elif last_type == LineTypes.CAPTION:
                return LineTypes.TIMESTAMP
            elif last_type == LineTypes.PAGE_MARK:
                return LineTypes.CAPTION
            else:
                return LineTypes.INVALID

        # PAGE_MARK or a BLOCK_LINE?
        elif re.findall(r"^\Page\s\d+$", line):
            return LineTypes.PAGE_MARK
        else:
            # must be a record block, but which line?
            if last_type == LineTypes.BLANK:
                return LineTypes.BLOCK_LINE_1
            elif last_type == LineTypes.BLOCK_LINE_1:
                return LineTypes.BLOCK_LINE_2
            elif last_type == LineTypes.BLOCK_LINE_2:
                return LineTypes.BLOCK_LINE_3
            elif last_type == LineTypes.BLOCK_LINE_3:
                return LineTypes.BLOCK_LINE_4
            elif last_type == LineTypes.BLOCK_LINE_4:
                return LineTypes.BLOCK_LINE_5
            else:
                return LineTypes.INVALID
    else:
        raise ValueError("get_line_type: line == None")


def parse_record_line(record=None, line=None, line_type=LineTypes.INVALID, location_regex=None):
    """Parse values from record line."""

    if not line:
        raise ValueError("parse_record_line: line == None")

    line = line.strip()

    # BLOCK_LINE_1
    if line_type == LineTypes.BLOCK_LINE_1:
        # get location
        m = re.search("^%s(.*?)([\\s\\.]b\\d+.$)" % (location_regex or r"(\w+)"), line)
        try:
            record["location"]    = m.group(1)
            record["call_number"] = str(m.group(2)).strip()
            record["bib_number"]  = m.group(3)

            # remove check digit and leading '.'
            record["bib_number"] = record["bib_number"][1:-1]
        except IndexError:
            sys.stderr.write("Error: parse_record_line: regex group out of bounds parsing BLOCK_LINE_1: %s\n" % (line))
            sys.exit(1)

        if not record["location"]:
            record["location"] = "ERROR"

        if not record["call_number"]:
            record["call_number"] = "ERROR"

        if not record["bib_number"]:
            record["bib_number"] = "ERROR"

#        if not record["location"] or not record["call_number"] or not record["bib_number"]:
#            raise ValueError("parse_record_line: record BLOCK_LINE_1 contains empty values")
            
    
    # BLOCK_LINE_2
    elif line_type == LineTypes.BLOCK_LINE_2:
        # try line as TITLE/AUTHOR...
        try:
            m = re.search(r"(^(.*)[\/])([^/]*$)", line)

            if not m:
                raise ValueError("parse_record_line: BLOCK_LINE_2 regex failed: %s" % (line))

            try:
                record["title"]   = m.group(2)
                record["author"]  = m.group(3)

            except ValueError:
                sys.stderr.write("Error: parse_record_line: regex group out of bounds parsing BLOCK_LINE_2: %s\n" % (line))
                sys.exit(1)

        except Exception:
            # or just take whole line as TITLE, set AUTHOR to ""
            record["title"] = line
            record["author"] = str(None)
            
        finally:
            if record["title"].endswith((".", ",")):
                record["title"] = record["title"][:-1]
            if record["author"].endswith((".", ",")):
                record["author"] = record["author"][:-1]

    # BLOCK_LINE_3
    elif line_type == LineTypes.BLOCK_LINE_3:
        record["publishing"] = line

        if record["publishing"].endswith("."):
            record["publishing"] = record["publishing"][:-1]

    # BLOCK_LINE_4
    elif line_type == LineTypes.BLOCK_LINE_4:
        record["pickup_location"] = line
        # most records lack VOLUME line...
        record["volume"] = " "

    # Looks like this block has volume info, so shift everything down a line... x_x
    # BLOCK_LINE_5
    elif line_type == LineTypes.BLOCK_LINE_5:
        # swap values to correct for VOLUME in line 3
        record["volume"] = record["publishing"]
        record["publishing"] = record["pickup_location"]
        record["pickup_location"] = line

    else:
        raise ValueError("parse_record_line: not a record block line: %d" % (line_type))


def set_record_flags(record, location_filter=None, scraper=None):
    """Sets flags for record."""

    flags = set() 

    if location_filter:
        location_filter = location_filter.upper()

    if not record:
        raise ValueError("get_record_flag: record == None")

    if not scraper:
        scraper = WebPACScraper(CATALOG_HOSTNAME)

    if not "available_count" in record:
        record["available_count"] = 0

    try:
        items = scraper.get_items_for_bib(record["bib_number"])

        for item in items:
            loc = item["location"].upper()

            # jump to next item if location doesn't match filter
            if location_filter and loc.find(location_filter) == -1:
                continue

            # for all AVAILABLE items
            if item["availability"] == "AVAILABLE" or item["availability"].upper() == "RECENTLY RETURNED":
                # update records with scraped location/call_number
                if (FAVOR_WEBPACSCRAPER_DATA):
                    record["location"] = item["location"]
                    record["call_number"] = item["call_number"]

                if item["availability"] == "AVAILABLE":
                    record["available_count"] += 1

                if loc.endswith("NEW"):
                    flags.add(FlagTypes.NEW)
                elif not loc.find("CLOSED STACK") == -1:
                    flags.add(FlagTypes.CLOSED_STACKS)
                elif not loc.find("OVERSIZE") == -1:
                    flags.add(FlagTypes.OVERSIZED)
                elif not loc.find("SHORT STOR") == -1:
                    flags.add(FlagTypes.SHORT_STORIES)

        # join flags for display
        if len(flags) > 0:
            record["flags"] = ",".join(flags)
        else:
            record["flags"] = " "

    except Exception, e:
        sys.stderr.write("Error: %s\nget_items_for_bib '%s' failed!\n" % (e, record["bib_number"]))
        record["flags"] = " "


def records_to_csv(records, filename=None):
    """Outputs CSV of records."""

    # flatten dict of dicts to list of dicts
    data = []
    for record in records:
        data.append(records[record])

    fields = ["requested_count",
              "available_count",
              "call_number",
              "author",
              "title",
              "volume",
              "bib_number",
              "flags"]

    headers = {"requested_count"    : "# Requested",
               "available_count"    : "# Available",
               "call_number"        : "Call #",
               "author"             : "Author",
               "title"              : "Title",
               "volume"             : "Volume",
               "bib_number"         : "Bib #",
               "flags"              : "Flags"}

    if INCLUDE_LOCATION_IN_CSV:
        fields.insert(0, "location")
        headers["location"] = "Location"

    if INCLUDE_PUBLISHING_IN_CSV:
        fields.append["publishing"]
        headers["publishing"] = "Publishing"

    if INCLUDE_PICKUP_LOCATION_IN_CSV:
        fields.append["pickup_location"]
        headers["pickup_location"] = "Pickup Location"

    # output csv
    try:
        if filename:
            file = open(filename, "wb")
            if WRITE_BOM_TO_CSV:
                file.write(codecs.BOM_UTF8)
        else:
            file = sys.stdout
            
        writer = csv.DictWriter(file, fieldnames=fields, delimiter=',', dialect=csv.excel, quoting=csv.QUOTE_ALL)
        writer.writerow(headers)

        # write rows sorted by "call_number"
        for row in sorted(sorted(data, key=operator.itemgetter("call_number")), key=operator.itemgetter("flags")):
            record = dict(row)

            if not INCLUDE_LOCATION_IN_CSV:
                del record["location"]

            if not INCLUDE_PUBLISHING_IN_CSV:
                del record["publishing"]

            if not INCLUDE_PICKUP_LOCATION_IN_CSV:
                del record["pickup_location"]

            writer.writerow(record)

    except Exception, e:
        sys.stderr.write("Error: records_to_csv: %s, [%s]\n" % (sys.exc_info(),filename))
        sys.exit(1)

    finally:
        if filename and file:
           file.close()


def records_to_xml(records, filename=None):
    """Serializes records to xml."""

    # guess current location
    location = records[records.keys()[0]]["location"]
    try:
        file = open(LOCATIONS_FILE)
        locations = sorted([line.strip() for line in file], key=len, reverse=True)
        for loc in locations:
            if location.startswith(loc):
                location = loc
                break

        file.close()
    except IOError:
        sys.stderr.write("Error: records_to_xml: unable to open file: %s\n" % (filename))

    # yeah...  x_x
    location_normalized = ''.join(i for i in location if i not in [' ','.'])
 
    # get WebPAC search id
    search_id = ""
    try:
        searchscope_table = scraper.get_searchscope_table()

        for k in searchscope_table:
            searchloc_normalized = ''.join(i for i in k if i not in [' ','.'])

            if location_normalized == searchloc_normalized:
                search_id = searchscope_table[k] 

    except Exception, e:
        sys.stderr.write("Error: location not found in searchscope:  %s, [%s]\n" % (sys.exc_info(),filename))

    # flatten dict of dicts to list of dicts
    data = []
    for record in records:
        data.append(records[record])

    search_baseurl = "http://%s:%s/search~S%s/,?" % (CATALOG_HOSTNAME, `CATALOG_PORT`, search_id)

    doc = Document()
    root_node = doc.createElement("paging_list")
    root_node.setAttribute("location", location)
    root_node.setAttribute("timestamp", str(datetime.now()))
    root_node.setAttribute("search_id", search_id)
    root_node.setAttribute("count", str(len(records)))
    root_node.setAttribute("search_baseurl", search_baseurl)
    doc.appendChild(root_node)

    for row in sorted(sorted(data, key=operator.itemgetter("call_number")), key=operator.itemgetter("flags")):
        record_to_xml(doc, root_node, row)

    if filename:
        try:
            file = open(filename, "w")
            doc.writexml(file)

        except:
            sys.stderr.write("Error: records_to_xml: unable to write to file: %s\n" % (filename))
            sys.exit(1)

        finally:
            file.close()

    else:
        print doc.toprettyxml(indent="   ")


def record_to_xml(doc, parent, record):
    """Adds <record /> node."""

    elem = doc.createElement("record")

 
    for key in record:
        v = escape(str(record[key]).strip())
        v = v.replace("'", "&#39;")
        v = v.replace('"', "&quot;")

        v = v.replace(",", "&#44;")

        e = doc.createElement(key)
        t = doc.createTextNode(v)

        e.appendChild(t)
        elem.appendChild(e)

    parent.appendChild(elem)


def main(argv=None):
    progopts = {"csv" : False,
                "output-file-csv" : None,
                "xml" : False,
                "output-file-xml" : None,
                "file" : None}
   
    try:
        options, remainder = getopt.gnu_getopt(sys.argv[1:], ":", ["file=",
                                                                   "xml",
                                                                   "csv",
                                                                   'output-file-csv=',
                                                                   'output-file-xml=',
                                                                   'version',
                                                                   'help',
                                                                   'usage'])
    except getopt.GetoptError, e:
        sys.stderr.write("Error: %s\n" % (e))
        sys.stderr.write(__doc__)
        sys.exit(1)

    if remainder:
        sys.stderr.write("Error: invalid options: %s\n\n" % (", ".join(remainder)))
        sys.stderr.write(__doc__)
        sys.exit(1)

    for opt, arg in options:
        if opt in ("--version"):
            print "squire v%s, by %s<%s>\n" % (__version__, __author__, __email__)
            sys.exit(0)
        elif opt in ("--help", "--usage"):
            sys.stderr.write(__doc__)
            sys.exit(0)
        elif opt in ("--csv"):
            progopts["csv"] = True
        elif opt in ("--output-file-csv"):
            progopts["output-file-csv"] = arg
        elif opt in ("--xml"):
            progopts["xml"] = True
        elif opt in ("--output-file-xml"):
            progopts["output-file-xml"] = arg
        elif opt in ("--file"):
            progopts["file"] = arg
   
    if not progopts["csv"] and not progopts["xml"]:
        sys.stderr.write("Error: no output mode selected (use '--csv' or '--xml')\n")
        sys.stderr.write(__doc__)
        sys.exit(1)

    if not progopts["file"]:
        sys.stderr.write("Error: no input file specified (use '--file=[FILENAME]')\n")
        sys.stderr.write(__doc__)
        sys.exit(1)

    # start processing records
    records = load_records(progopts["file"])

    if progopts["xml"]:
        # XML output
        records_to_xml(records, progopts["output-file-xml"])
   
    if progopts["csv"]:   
        # CSV output
        records_to_csv(records, progopts["output-file-csv"])


if __name__ == "__main__":
    main()
