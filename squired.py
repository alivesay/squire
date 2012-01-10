#!/opt/ActivePython-2.7/bin/python

import os
import sys
import errno
import logging
import csv
import smtplib
import codecs
import operator
import mmap
import re
from signal import SIGINT,SIGTERM
from time import sleep,time
from subprocess import Popen, PIPE
from shutil import move
from grp import getgrnam
from pwd import getpwnam
from datetime import datetime
from subprocess import Popen
from email import encoders
from email.MIMEBase import MIMEBase
from email.MIMEText import MIMEText
from email.mime.multipart import MIMEMultipart
from email import Encoders
from email import Charset
from xml.dom.minidom import Document
from cgi import escape

# http://pypi.python.org/pypi/python-daemon/1.5.5 (PEP 3143 reference)
# NOTE: requires patches/python-daemon-1.5.5_lockfile_0.9.1_fix.patch
import daemon
import daemon.pidlockfile

# http://pypi.python.org/pypi/lockfile/0.9.1
from lockfile import LockTimeout

# src/squiredmodule.c extension
import squired


################################################################################
# config
################################################################################

HELP_DESK_EMAIL = "help.desk@yourlibrary.org"   # displayed in paging list email
HELP_DESK_PHONE = "x85100" 

DIR_CONFIG  = "/etc/squired"
DIR_VAR     = "/var/lib/squired"
DIR_LOG     = "/var/log/squired"
DIR_RUN     = "/var/run/squired"

DIR_OUTPUT  = os.path.join(DIR_VAR, "output")  # where CSV & XML files are saved
DIR_ARCHIVE = os.path.join(DIR_VAR, "archive") # where paging lists are archived

SQUIRE_CMD   = "/opt/squired/squire.py"
XSLTPROC_CMD = "/usr/bin/xsltproc"

LOG_FILE    = os.path.join(DIR_LOG, "squired.log")
LOG_LEVEL   = logging.INFO
LOG_FORMAT  = "%(asctime)s %(levelname)s (%(funcName)s:%(lineno)d) %(message)s"

SEND_EMAIL_AS = "squired@catalog.yourlibrary.org"

SMTP_SERVER = "localhost"
SMTP_PORT   = 25

LOCATION_EMAILS_FILE  = os.path.join(DIR_CONFIG, "locationemails.cfg")
SQUIRE_T_2XHTML_XSL_FILE = os.path.join(DIR_CONFIG, "squiret2xhtml.xsl")
SQUIRE_I_2XHTML_XSL_FILE = os.path.join(DIR_CONFIG, "squirei2xhtml.xsl")

DAEMON_WORKING_DIR  = DIR_VAR
DAEMON_LOCKFILE     = os.path.join(DIR_RUN, "squired.pid")
DAEMON_USER         = "root" # don't use 'root'
DAEMON_GROUP        = "root" # don't use 'root'
DAEMON_UMASK        = 0o002

################################################################################


################################################################################
# advanced config
################################################################################
#
# DANGER!!!!!!!!!!!!!!!!!!!
#
# WARNING: squired.py will automatically move any files with names ending with
# PAGING_LIST_EXT from DIR_DROPBOX into DIR_ARCHIVE.  At this time, Millennium's
# Auto Notice "FTS file save" will save files to '/iiidb/circ/autonotices'.
#
# It is recommended you leave these settings alone, and configure all jobs to
# print to "FTS file save" files named with '.paginglist.p' extensions.
# 
# Ok?
#

DIR_DROPBOX     = "/iiidb/circ/autonotices" # where paging lists appear
PAGING_LIST_EXT = ".paginglist"   # file extension used for FTS notices
ITEM_LIST_EXT   = ".itemlist" 
IGNORE_DOTFILES = True            # ignore dotfiles in DIR_DROPBOX

TIMESTAMP_FORMAT = "%Y-%m-%d"     # add h,m,s if running multiple times per day
TIMESTAMP_ACTIVE = True

LISTS_URL = "http://catalog.yourlibrary.org:8000"
LISTS_DIR = "/var/www/html/"

ITEM_LIST_TIMEOUT = 900          # in seconds, how long to wait for item lists

################################################################################


class ListTypes:
    INVALID           = 0
    TITLE_PAGING_LIST = 1
    ITEM_PAGING_LIST  = 2

    @staticmethod
    def gettypestr(type=0):
        if type == ListTypes.TITLE_PAGING_LIST:
            return "Title"
        elif type == ListTypes.ITEM_PAGING_LIST:
            return "Item"
        else:
            return "INVALID"

class INotify:
    """Linux kernel inotify API wrapper"""

    # inotify_event masks
    IN_ACCESS        = 0x00000001 # File was accessed
    IN_MODIFY        = 0x00000002 # File was modified
    IN_ATTRIB        = 0x00000004 # Metadata changed
    IN_CLOSE_WRITE   = 0x00000008 # Writable file was closed
    IN_CLOSE_NOWRITE = 0x00000010 # Unwrittable file closed
    IN_OPEN          = 0x00000020 # File was opened
    IN_MOVED_FROM    = 0x00000040 # File was moved from X
    IN_MOVED_TO      = 0x00000080 # File was moved to Y
    IN_CREATE        = 0x00000100 # Subfile was created
    IN_DELETE        = 0x00000200 # Subfile was deleted
    IN_DELETE_SELF   = 0x00000400 # Self was deleted
    IN_MOVE_SELF     = 0x00000800 # Self was moved

    __wd    = None
    __procs = None


    def __init__(self, path, opts):
        squired.inotify_init()
        self.__wd = squired.add_watch(path, opts)
        if self.__wd < 0:
            sys.stderr.write("Error: inotify_add_watch failed for '%s' [%s]\n" % (path, errno.errorcode[squired.errno()]))
            sys.exit(1)


    def __del__(self):
        if self.__wd is not None:
            squired.rm_watch(self.__wd)
            squired.shutdown()


    def get_events(self):
        """Returns list of events if available, otherwise a message dict is returned."""

        return squired.select()


class SquireDaemon:
    """Monitors a directory for new Millennium pagings lists and spins them into gold."""
    
    __logger            = None
    __is_running        = False
    __location_emails   = {}
    __item_list_infos   = {}


    def __init__(self):
        self.__logger = logging.getLogger("squired")
        self.__is_running = True


    def init_logger(self):
        """Setup logging facility."""

        hdlr = logging.FileHandler(LOG_FILE)
        formatter = logging.Formatter(LOG_FORMAT)
        hdlr.setFormatter(formatter)
        self.__logger.addHandler(hdlr)
        self.__logger.setLevel(LOG_LEVEL)

        self.__logger.info("Starting up...")


    def load_location_emails(self):
        """Load table mapping locations to email addresses."""

        try:
            for row in csv.reader(open(LOCATION_EMAILS_FILE, "rb")):
                 self.__location_emails[row[0]] = row[1].split("|")

        except NameError, e:
            self.__logger.info("%s [%s]" % (e.strerror, LOCATION_EMAILS_FILE))

        except:
            self.__logger.info(sys.exc_info()[1])


    def shutdown(self):
        """The candle that burns twice as bright burns half as long."""

        # close log handler
        self.__logger.info("Shutting down...")
        logging.shutdown()


    def stop(self, signal=None, frame=None):
        """Stop SquireDaemon service."""

        self.__is_running = False


    def process_file(self, filename=None):
        """Moves new paging list file to DIR_ARCHIVE and processes with SQUIRE_CMD."""

        if not PAGING_LIST_EXT:
            self.__logger.info("Error: PAGING_LIST_EXT not defined!")
            return

        if not ITEM_LIST_EXT:
            self.__logger.info("Error: ITEM_LIST_EXT not defined!")
            return

        if IGNORE_DOTFILES and filename[0] == '.':
            return
       
        ext_timestamp = datetime.now().strftime("%y%m%d")

        # ONLY process files with names ending in PAGING_LIST_EXT
        title_list_ext = "%s.t%s.auton" % (PAGING_LIST_EXT, ext_timestamp)
        item_list_ext  = "%s.t%s.auton" % (ITEM_LIST_EXT, ext_timestamp)

        # title list or item list?
        if filename.endswith(title_list_ext):
            paging_list_ext = title_list_ext
            list_type = ListTypes.TITLE_PAGING_LIST

        elif filename.endswith(item_list_ext):
            paging_list_ext = item_list_ext
            list_type = ListTypes.ITEM_PAGING_LIST

        else:
            # ignore unknown file type
            return

        basename  = filename[:-len(paging_list_ext)]
        extension = paging_list_ext

        if TIMESTAMP_ACTIVE:
            timestamp = datetime.now().strftime(TIMESTAMP_FORMAT)
            timestamp_spacer = "_"
        else:
            timestamp = ""
            timestamp_spacer = ""

        fullpath_file    = os.path.normpath(os.path.join(DIR_DROPBOX, filename))
        fullpath_archive = os.path.normpath(os.path.join(DIR_ARCHIVE, filename))
        fullpath_csv     = os.path.normpath(os.path.join(DIR_OUTPUT, "%s%s%s%s.csv" % (basename, ListTypes.gettypestr(list_type), timestamp_spacer, timestamp)))
        fullpath_xml     = os.path.normpath(os.path.join(DIR_OUTPUT, "%s%s%s%s.xml" % (basename, ListTypes.gettypestr(list_type), timestamp_spacer, timestamp)))

        self.__logger.info("A wild paging list approaches! [%s]" % (fullpath_file))

        if list_type == ListTypes.TITLE_PAGING_LIST:
            self.process_title_list(basename, timestamp, fullpath_file, fullpath_archive, fullpath_csv, fullpath_xml)

        elif list_type == ListTypes.ITEM_PAGING_LIST:
            self.process_item_list(basename, timestamp, fullpath_file, fullpath_archive, fullpath_csv, fullpath_xml)


    def process_title_list(self, basename, timestamp, fullpath_file, fullpath_archive, fullpath_csv, fullpath_xml):
        try:
            # move new paging list to archive directory
            move(fullpath_file, fullpath_archive)

        except IOError, e:
            self.__logger.info("Error: %s" % (e.strerror))
        except Exception, e:
            self.__logger.info("Error: %s" % (sys.exc_info()))

        try:
            os.chmod(fullpath_archive, 0664)

        except IOError, e:
            self.__logger.info("Error: %s" % (e.strerror))
        except Exception, e:
            self.__logger.info("Error: %s" % (sys.exc_info()))

        # run subprocess to process paging list
        try:
            proc = Popen([SQUIRE_CMD,
                         "--file=%s" % (fullpath_archive),
                         "--csv",
                         "--output-file-csv=%s" % (fullpath_csv),
                         "--xml",
                         "--output-file-xml=%s" % (fullpath_xml)],
                         close_fds=False,
                         stderr=PIPE)

            # hijack proc
            proc.paging_list_fullpath_file     = fullpath_file
            proc.paging_list_fullpath_archive  = fullpath_archive
            proc.paging_list_fullpath_csv      = fullpath_csv
            proc.paging_list_fullpath_xml      = fullpath_xml
            proc.paging_list_basename          = basename
            proc.paging_list_timestamp         = timestamp
            proc.paging_list_waiting_for_items = False
            proc.paging_list_info_key          = "%s%s" % (basename, timestamp)

            # add to process list
            self.__procs.append(proc)

        except IOError, e:
            self.__logger.info("Error: %s" % (e.strerror))
        except OSError, e:
            self.__logger.info("Error: SQUIRE_SMD failed %s [%s]" % (e.strerror, SQUIRE_CMD))
        except Exception, e:
            self.__logger.info("Error: %s" % (sys.exc_info()))

    
    def process_item_list(self, basename, timestamp, fullpath_file, fullpath_archive, fullpath_csv, fullpath_xml):
        records = []

        try:
            # move new item list to archive directory
            move(fullpath_file, fullpath_archive)

        except IOError, e:
            self.__logger.info("Error: %s" % (e.strerror))
        except Exception, e:
            self.__logger.info("Error: %s" % (sys.exc_info()))


        try:
            os.chmod(fullpath_archive, 0664)

        except IOError, e:
            self.__logger.info("Error: %s" % (e.strerror))
        except Exception, e:
            self.__logger.info("Error: %s" % (sys.exc_info()))


        try:
            # load items from Item Paging List
            f = os.open(fullpath_archive, os.O_RDONLY)
            map = mmap.mmap(f, 0, prot=mmap.PROT_READ)

            item_regex = r"(^\s*AUTHOR:|^\s*Please\ pull\ this\ item\ and\ check\ it\ in\ to\ place\ in\ transit:\s*)(\s*AUTHOR:|)(.*?)^(\s*TITLE:|\s*)(.*?)^(\s*IMPRINT:|\s*)(.*?)^(\s*PUB\ DATE:|\s*)(.*?)^(\s*DESC:|\s*)(.*?)^(\s*CALL\ NO:|\s*)(.*?)^(\s*VOLUME:|\s*)(.*?)^(\s*BARCODE:|\s*)(.*?)^(\s*STATUS:|\s*)(.*?)^(\s*REC\ NO:|\s*)(.*?)^(\s*LOCATION:|\s*)(.*?)^(\s*PICKUP AT:|\s*)(.*?)^(\s*OPACMSG:|\s*)(.*?)$"
            
            m = re.findall(item_regex, map, re.DOTALL|re.MULTILINE)

            for l in m:
                record = {}
                record["author"]          = l[2].strip()  # AUTHOR:
                record["title"]           = l[4].strip()  # TITLE:
                record["call_number"]     = l[12].strip() # CALL NO:
                barcode                   = l[16].strip()  # BARCODE:
                record["location"]        = l[22].strip() # LOCATION:
       
                # format barcode
                if len(barcode) > 13: 
                    record["barcode"] = "%s %s %s %s" % (barcode[0], barcode[1:5], barcode[5:10], barcode[10:])
                else:
                    record["barcode"] = barcode
                records.append(record)

            map.close()
            os.close(f)

        except Exception, e:
            self.__logger.info("Error: %s" % (sys.exc_info()))

        # dump to csv
        fields = ["location", "call_number", "author", "title", "barcode"]

        headers = {"location" : "Location", "call_number" : "Call #", "author" : "Author", "title" : "Title", "barcode" : "Barcode"}

        # output csv
        try:
            csv_file = open(fullpath_csv, "wb")
            csv_file.write(codecs.BOM_UTF8)
            
            writer = csv.DictWriter(csv_file, fieldnames=fields, delimiter=',', dialect=csv.excel, quoting=csv.QUOTE_ALL)
            writer.writerow(headers)

            # write rows sorted by "call_number"
            for row in sorted(records, key=operator.itemgetter("call_number")):
                writer.writerow(row)

        except Exception, e:
            self.__logger.info("Error writing item csv : %s [%s]" % (sys.exc_info(), fullpath_csv))

        finally:
            if csv_file:
               csv_file.close()

        # dump to xml
        branch_name = basename.replace("_"," ")

        doc = Document()
        root_node = doc.createElement("paging_list")
        root_node.setAttribute("location", branch_name)
        root_node.setAttribute("timestamp", str(datetime.now()))
        root_node.setAttribute("count", str(len(records)))
        doc.appendChild(root_node)

        for record in sorted(records, key=operator.itemgetter("call_number")):
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

            root_node.appendChild(elem)

        try:
            xml_file = open(fullpath_xml, "w")
            doc.writexml(xml_file)

        except Exception, e:
            self.__logger.info("Error writing item xml : %s [%s]" % (sys.exc_info(), fullpath_xml))

        finally:
            xml_file.close()

        # save item list info
        info_key = "%s%s" % (basename, datetime.now().strftime(TIMESTAMP_FORMAT))
        self.__item_list_infos[info_key] = {}
        self.__item_list_infos[info_key]["fullpath_archive"] = fullpath_archive
        self.__item_list_infos[info_key]["fullpath_csv"] = fullpath_csv
        self.__item_list_infos[info_key]["fullpath_xml"] = fullpath_xml
        self.__item_list_infos[info_key]["created_at"]   = time()

    
    def post_processing(self, p_info):

        branch_name = p_info["basename"].replace("_"," ")

        # generate title HTML 
        title_html_filename = os.path.normpath(os.path.join(DIR_OUTPUT, "%s%s_%s.html" % (p_info["basename"], ListTypes.gettypestr(ListTypes.TITLE_PAGING_LIST), p_info["timestamp"])))

        try:
            # generate HTML file
            # blocking is probably okay here...maybe...
            p = Popen("%s -o %s %s %s" % (XSLTPROC_CMD, title_html_filename, SQUIRE_T_2XHTML_XSL_FILE, p_info["title_list_fullpath_xml"]), shell=True)
            sts = os.waitpid(p.pid, 0)[1]

            # create symlink
            latest_title_link     = "%s%s/latest_title.html" % (LISTS_DIR, p_info["basename"])
            latest_raw_title_link = "%s%s/latest_title_raw.txt" % (LISTS_DIR, p_info["basename"])

            try:
                os.remove(latest_title_link)
            except:
                pass

            try:
                os.symlink(title_html_filename, latest_title_link)
            except:
                self.__logger.info(sys.exc_info()[1])


            try:
                os.remove(latest_raw_title_link)
            except:
                pass

            try:
                os.symlink(p_info["title_list_fullpath_archive"], latest_raw_title_link)
            except:
                self.__logger.info(sys.exc_info()[1])

        except:
            self.__logger.info(sys.exc_info()[1])

        if p_info["has_item_list"]:
            # generate item HTML, if available
            item_html_filename = os.path.normpath(os.path.join(DIR_OUTPUT, "%s%s_%s.html" % (p_info["basename"], ListTypes.gettypestr(ListTypes.ITEM_PAGING_LIST), p_info["timestamp"])))

            try:
                # generate HTML file
                # blocking is probably okay here...maybe...
                p = Popen("%s -o %s %s %s" % (XSLTPROC_CMD, item_html_filename, SQUIRE_I_2XHTML_XSL_FILE, p_info["item_list_fullpath_xml"]), shell=True)
                sts = os.waitpid(p.pid, 0)[1]

                # create symlink
                latest_item_link = "%s%s/latest_item.html" % (LISTS_DIR, p_info["basename"])
                latest_raw_item_link = "%s%s/latest_item_raw.txt" % (LISTS_DIR, p_info["basename"])

                try:
                    os.remove(latest_item_link)
                except:
                    pass

                try:
                    os.symlink(item_html_filename, latest_item_link)
                except:
                    self.__logger.info(sys.exc_info()[1])

                try:
                    os.remove(latest_raw_item_link)
                except:
                    pass

                try:
                    os.symlink(p_info["item_list_fullpath_archive"], latest_raw_item_link)
                except:
                    self.__logger.info(sys.exc_info()[1])

            except:
                self.__logger.info(sys.exc_info()[1])


        # send email
        if branch_name in self.__location_emails:

            msg = MIMEMultipart("alternative")
            Charset.add_charset("utf-8", Charset.QP, Charset.QP, "utf-8")

            msg["Subject"]  = "%s Paging Lists for %s" % (branch_name, p_info["timestamp"])
            msg["From"]     = SEND_EMAIL_AS
            msg["To"]       = ", ".join(self.__location_emails[branch_name])
            msg["Preamble"] = msg["Subject"]
            branch_url  = "%s/%s/index.html" % (LISTS_URL, p_info["basename"])
            # TODO: replace these with external templates
            body_text = "\n Please visit %s to access the %s Library paging lists.\n\nSpreadsheet versions of your paging lists are also attached to this email.\n\nIf there is a problem with your paging lists, please contact the Help Desk at %s or %s." % (branch_url, branch_name, HELP_DESK_EMAIL, HELP_DESK_PHONE)
            body_html = """\
                        <html>
                        <head></head>
                        <body>
                        <p>Please visit <a href='%s'>here</a> to access the %s Library paging lists.</p>
                        <p>Spreadsheet versions of your paging lists are also attached to this email.</p>
                        <p>If there is a problem with your paging lists, please contact the Help Desk at <a href='mailto:%s'>%s</a> or %s.</p>
                        </body>
                        </html>
                        """ % (branch_url, branch_name, HELP_DESK_EMAIL, HELP_DESK_EMAIL, HELP_DESK_PHONE)
          
            try:
                # load and encode Title csv file
                title_csv_attachment_file = open(p_info["title_list_fullpath_csv"], "rb")
                title_csv_attachment = MIMEBase("text","csv")
                title_csv_attachment.set_payload(title_csv_attachment_file.read())
                Encoders.encode_7or8bit(title_csv_attachment)
                title_csv_attachment.add_header('Content-Disposition', 'attachment', filename=os.path.basename(p_info["title_list_fullpath_csv"]))
                msg.attach(title_csv_attachment)
                title_csv_attachment_file.close()

                # load and encode Title html file
                title_html_attachment_file = open(title_html_filename, "rb")
                title_html_attachment = MIMEBase('application', 'octet-stream')
                title_html_attachment.set_payload(title_html_attachment_file.read())
                Encoders.encode_base64(title_html_attachment)
                title_html_attachment.add_header('Content-Disposition', 'attachment', filename=os.path.basename(title_html_filename))
                msg.attach(title_html_attachment)
                title_html_attachment_file.close()

            except:
                self.__logger.info(sys.exc_info()[1])

            # add Item attachements, if available
            if p_info["has_item_list"]:
                try:
                    # load and encode Item csv file
                    item_csv_attachment_file = open(p_info["item_list_fullpath_csv"], "rb")
                    item_csv_attachment = MIMEBase("text","csv")
                    item_csv_attachment.set_payload(item_csv_attachment_file.read())
                    Encoders.encode_7or8bit(item_csv_attachment)
                    item_csv_attachment.add_header('Content-Disposition', 'attachment', filename=os.path.basename(p_info["item_list_fullpath_csv"]))
                    msg.attach(item_csv_attachment)
                    item_csv_attachment_file.close()

                    # load and encode Item html file
                    item_html_attachment_file = open(item_html_filename, "rb")
                    item_html_attachment = MIMEBase('application', 'octet-stream')
                    item_html_attachment.set_payload(item_html_attachment_file.read())
                    Encoders.encode_base64(item_html_attachment)
                    item_html_attachment.add_header('Content-Disposition', 'attachment', filename=os.path.basename(item_html_filename))
                    msg.attach(item_html_attachment)
                    item_html_attachment_file.close()

                except:
                 self.__logger.info(sys.exc_info()[1])

            # attach body
            msg.attach(MIMEText(body_text, "plain"))
            msg.attach(MIMEText(body_html, "html"))

            # send mail
            s = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
            s.sendmail(SEND_EMAIL_AS, self.__location_emails[branch_name], msg.as_string())
            s.quit()

            self.__logger.info("%s paging list emailed to %s" % (branch_name, msg["To"]))


    def start(self):
        """Starts SquireDaemon service."""

        # initialize logging
        self.init_logger()

        # load location -> email table
        self.load_location_emails()

        # initialize inotify monitoring
        inotify = INotify(DIR_DROPBOX, INotify.IN_CLOSE_WRITE)

        self.__procs = list()

        # poll for inotify events
        while self.__is_running:

            events = inotify.get_events()

            # events were found
            if type(events) is list:
                for event in events:
                    # IN_CLOSE_WRITE event?
                    if (event["mask"] & INotify.IN_CLOSE_WRITE):
                        self.process_file(event["name"])

            # message dict returned
            elif type(events) is dict:
                if "ERROR" in events:
                    self.__logger.info("Error: %s" % (events["ERROR"]))
                elif "OKAY" in events:
                    # TIMEOUT or BUSY
                    pass
                else:
                    self.__logger.info("Error: unknown message dict returned")

            # unknown type returned
            else:
                self.__logger.info("Error: unknown type: %s\n" % (str(type(events))))

            # query procs
            for proc in self.__procs:
                proc.poll()

                # process finished?
                if proc.returncode is not None:
                    # cleanup any stale items infos (will happen if a day's item list is processed after title proc's ITEM_LIST_TIMEOUT)
                    for k in self.__item_list_infos:
                        if time() - self.__item_list_infos[k]["created_at"] > 86400: # 24 hours
                            self.__logger.info("Removed stale item info [%s]!" % k)
                            del self.__items_list_infos[k]

                    if proc.returncode == 0:
                        if proc.paging_list_waiting_for_items == False:
                            # log any errors
                            for line in proc.stderr:
                                self.__logger.info("[%d] %s" % (proc.pid, line.rstrip()))
 
                            # start waiting for item list
                            self.__logger.info("[%d] %s processed successfully" % (proc.pid, proc.paging_list_fullpath_file))
                            self.__logger.info("[%d] Waiting for item list..." % (proc.pid))
                            proc.paging_list_waiting_for_items = True
                            proc.paging_list_wait_timestamp = time()
                        
                        else:
                            wait_time = time() - proc.paging_list_wait_timestamp

                            if proc.paging_list_info_key in self.__item_list_infos:
                                # process title list with item list
                                processing_info = {}
                                processing_info["has_item_list"]               = True
                                processing_info["timestamp"]                   = proc.paging_list_timestamp
                                processing_info["basename"]                    = proc.paging_list_basename
                                processing_info["title_list_fullpath_archive"] = proc.paging_list_fullpath_archive
                                processing_info["title_list_fullpath_csv"]     = proc.paging_list_fullpath_csv
                                processing_info["title_list_fullpath_xml"]     = proc.paging_list_fullpath_xml
                                try:
                                    item_list_fullpath_archive = self.__item_list_infos[proc.paging_list_info_key]["fullpath_archive"]
                                    item_list_fullpath_csv = self.__item_list_infos[proc.paging_list_info_key]["fullpath_csv"]
                                    item_list_fullpath_xml = self.__item_list_infos[proc.paging_list_info_key]["fullpath_xml"] 
                                    processing_info["item_list_fullpath_archive"] = item_list_fullpath_archive
                                    processing_info["item_list_fullpath_csv"]     = item_list_fullpath_csv
                                    processing_info["item_list_fullpath_xml"]     = item_list_fullpath_xml
                                except:
                                    self.__logger.info("[%d] Errr.. can't find item list for this one.. [%s]" % (proc.pid, proc.paging_list_basename))
                                    processing_info["has_item_list"] = False
    
                                del self.__item_list_infos[proc.paging_list_info_key]

                                self.post_processing(processing_info)

                                self.__procs.remove(proc)

                            elif wait_time > ITEM_LIST_TIMEOUT:
                                # process title list without item list
                                processing_info = {}
                                processing_info["has_item_list"]           = False
                                processing_info["basename"]                = proc.paging_list_basename
                                processing_info["timestamp"]               = proc.paging_list_timestamp
                                processing_info["title_list_fullpath_archive"] = proc.paging_list_fullpath_archive
                                processing_info["title_list_fullpath_csv"] = proc.paging_list_fullpath_csv
                                processing_info["title_list_fullpath_xml"] = proc.paging_list_fullpath_xml

                                if proc.paging_list_info_key in self.__item_list_infos:
                                    del self.__item_list_infos[proc.paging_list_info_key]
                                
                                self.post_processing(processing_info)

                                self.__procs.remove(proc)
                    else:
                        # log any errors
                        for line in proc.stderr:
                            self.__logger.info("[%d] [%s] %s" % (proc.pid, proc.paging_list_basename, line.rstrip()))
 
                        self._logger.info("[%d] Error: Non-zero return code on SQUIRE_CMD" % (proc.pid))
                        self.__procs.remove(proc)
        
        # cleanup
        self.shutdown()

 
if __name__ == "__main__":

    # get daemon uid/gid
    daemon_uid = getpwnam(DAEMON_USER).pw_uid
    daemon_gid = getgrnam(DAEMON_GROUP).gr_gid

    # create required directories if they don't exist
    for dir in DIR_VAR, DIR_RUN, DIR_LOG, DIR_OUTPUT, DIR_ARCHIVE, DAEMON_WORKING_DIR:
        try:
            os.makedirs(dir)
        except OSError:
            pass
        try:
            os.chown(dir, daemon_uid, daemon_gid)
        except OSError:
            pass

    sd = SquireDaemon()

    pid_lock = daemon.pidlockfile.TimeoutPIDLockFile(DAEMON_LOCKFILE, acquire_timeout=3, threaded=False)
 
    # setup DaemonContext and start the service
    context = daemon.DaemonContext(working_directory = DAEMON_WORKING_DIR,
                                   umask             = DAEMON_UMASK,
                                   pidfile           = pid_lock,
                                   uid               = daemon_uid,
                                   gid               = daemon_gid,
                                   stderr=sys.stderr,
                                   stdout=sys.stdout)


    context.signal_map = {SIGTERM : sd.stop,
                          SIGINT  : sd.stop}

    try:
        with context:
            sd.start()

    except LockTimeout:
        sys.stderr.write("Error: Lock request on '%s' timed out\n" % (DAEMON_LOCKFILE))

       

