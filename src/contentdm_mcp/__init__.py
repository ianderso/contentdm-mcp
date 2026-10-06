"""An MCP server for the record images that US state archives publish on CONTENTdm.

CONTENTdm is OCLC's hosted digital-collections platform. Most US state
archives and state libraries use it for their digitised records, and every
instance answers the same keyless JSON API and IIIF image service. The server
ships a curated list of those instances, searches one or several of them,
reads an item and its pages, and downloads a page image. Nothing here writes
anywhere, and nothing here keeps a family tree.
"""

__version__ = "0.1.0"
