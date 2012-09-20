squire
======

This set of scripts provides a method to generate customized Millennium ILS item and title paging lists.

Paging lists must be configured as `FTS file save` and must use the extensions  `.paginglist.p` for title lists and `.itemlist.p` for item lists.

The squired daemon (`squired.py`) uses the inotify Linux subsystem to monitor `/iiidb/circ/autonotices` for new paging lists and will begin processing once Millennium has closed the file.

Once a paging list is processed, squire will:

* email .csv and .html version to branch mailing lists
* maintain web directory of paging list files
* archive the raw and processed lists
* record activity to log file

**NOTE**: Due to some *insane* abuse of regular expressions, squire requires a version of Python compiled with `--enabled-unicode=ucs4`!


###squire.py

This may be used as standalone tool for processing title paging lists.  (Breaking out the item processing into a dedicated tool is on the TODO list.)

```
$ squire.py --help

Usage: squire [--csv [--output-file-csv=FILENAME]] [--xml [--output-file-xml=FILENAME]] --file=FILENAME

Processes Millennium Title Paging List into structured formats.
```