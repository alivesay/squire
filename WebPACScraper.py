#!/usr/bin/python

import sys
import urllib2
import re
import htmllib

HTTP_TIMEOUT = 10   # in seconds

class WebPACScraper:
    def __init__(self, server, port=80):
        self.__server = server
        self.__port = port
        self.__item_search_string = r"http://%s/search~S1?%s/.%s/.%s/1%%2C1%%2C1%%2CB/marc~%s"
        self.__http_timeout = HTTP_TIMEOUT


    def get_items_for_bib(self, bib_number):
        """Returns item info for bib_number."""

        if not bib_number:
            raise ValueError("get_items_for_bib: bib_number == None")

        items = []
        item_table = None

        # TODO: retry on timeout/unavailable 
        url = self.__item_search_string % (self.__server, `self.__port`, bib_number, bib_number, bib_number)
        content = urllib2.urlopen(url, None, self.__http_timeout).read()

        # find bibItems table
        m = re.search('class="bibItems".*?>(.*?)</table>', content, re.DOTALL)

        if m:
            item_table = m.group(1)
            m = re.findall(r"\<!-- field 1 --\>(.*?)</td>", item_table, re.DOTALL)
        else:
            raise Exception("get_items_for_bib: no bibItems table found at: %s" % (url))

        # get item locations
        if m:
            for v in m:
                v = self.unescape(v).strip()
                items.append({"location" : v})
        else:
            raise Exception("get_items_for_bib: couldn't get location field for: %s" % (bib_number))

        # get item call numbers
        m = re.findall(r"\<!-- field C --\>(.*?)</td>", item_table, re.DOTALL)

        if m:
            for i,v in enumerate(m):
                r = re.search(r"<a\b[^>]*>(.*?)</a>", v)
                try:
                    call_number = r.group(1)
                    items[i]["call_number"] = call_number

                except IndexError:
                    raise Exception("get_items_for_bib: regex group out of bounds: %s" % (v))

        # get item availabilities
        m = re.findall(r"\<!-- field \% --\>(.*?)</td>", item_table, re.DOTALL)

        if m:
            for i,v in enumerate(m):
                v = self.unescape(v).strip()
                items[i]["availability"] = v
        else:
            raise Exception("get_items_for_bib: couldn't get availability field for: %s" % (bib_number))

        # TODO: could probably get item numbers from "<!-- field # -->" but it looks like those are suppresed in webpac?

        return items

    def get_searchscope_table(self):
        """Gets available search scopes."""

        searchscopes = {}

        url = "http://%s:%s" % (self.__server, self.__port)
        content = urllib2.urlopen(url, None, self.__http_timeout).read()

        # find searchscope select
        m = re.search(r"id=\"searchscope\"\.*?>(.*?)\</select>", content, re.DOTALL)

        if m:
            options = m.group(1)
            n = re.findall(r"value=\"(.*?)\".*?>(.*?)\</option", options, re.DOTALL)
            for i in n:
                searchscopes[i[1].strip()] = i[0]

        else:
            raise Exception("get_searchscope_table: couldn't get select of searchscopes")

        return searchscopes


    def unescape(self, s):
        """Quick and dirty unescaping."""

        s = s.replace("&nbsp;","")
        p = htmllib.HTMLParser(None)
        p.save_bgn()
        p.feed(s)

        return p.save_end()

