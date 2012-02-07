<?xml version="1.0"?>
<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">

<xsl:template match="/">
<html>
<head>
<title>Item Paging List</title>
<style type="text/css">
#paginglist tbody tr:hover td {
    color: #111111;
    background-color: #d2dcf4;
}

#paginglist td {
    color: #333333;
    padding-top: 4px;
    padding-right: 8px;
    padding-bottom: 4px;
    padding-left: 8px;
}

tr.paginglist-alt {
    background-color: #DDDDDD;
}

#paginglist {
    font-family: sans-serif;
    font-size: 12px;
    border-collapse: collapse;
    text-align: left;
}

#paginglist th {
    padding-top: 4px;
    padding-right: 8px;
    padding-bottom: 4px;
    padding-left: 8px;
    font-weight: bold;
}

td, th {
    display: table-cell;
    vertical-align: middle;
}

tr {
    vertical-align: middle;
}

table {
    border-collapse: separate;
    border-spacing: 2px;
    width: auto;
}

.nowrap {
    white-space: nowrap;
}

body {
    font: sans-serif;
    color: #333333;
}

p.flags {
    font-size: small;
}

</style>
</head>
<body>
<xsl:apply-templates />
</body>
</html>
</xsl:template>

<xsl:template match="paging_list">
    <h2><xsl:value-of select="@location" /> Item Paging List - <xsl:value-of select="@timestamp" /></h2>
    <h3>Count: <xsl:value-of select="@count" /></h3>
    <table id="paginglist">
        <thead>
            <tr>
                <th>Location</th>
                <th class="nowrap">Call #</th>
                <th>Author</th>
                <th>Title</th>
                <th>Barcode</th>
            </tr>
        </thead>
        <tbody>
        <xsl:apply-templates />
        </tbody>
  </table>  
</xsl:template>

<xsl:template match="record">
    <xsl:choose>
      <xsl:when test="position() mod 2 = 0"><xsl:text disable-output-escaping="yes">&lt;tr&gt;</xsl:text></xsl:when>
      <xsl:otherwise><xsl:text disable-output-escaping="yes">&lt;tr class="paginglist-alt"&gt;</xsl:text></xsl:otherwise>
    </xsl:choose>
        <td><xsl:value-of select="location" disable-output-escaping="yes" /></td>
        <td><xsl:value-of select="call_number" disable-output-escaping="yes" /></td>
        <td><xsl:value-of select="author" disable-output-escaping="yes" /></td>
        <td><xsl:value-of select="title" disable-output-escaping="yes" /></td>
        <td><xsl:value-of select="barcode" disable-output-escaping="yes" /></td>
    <xsl:text disable-output-escaping="yes">&lt;/tr&gt;</xsl:text>
</xsl:template>

</xsl:stylesheet>
