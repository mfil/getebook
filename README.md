Getebook
========

Getebook is a package for building EPUB files from books that are published in
the form of webpages.

Description
-----------
Getebook contains the EbookParser class to extract the book content from the
webpage and find the link to the next page of the book. The parser works
together with an EpubBuilder object from the epub submodule to build the ebook
file. (Hopefully, I'll get around to add a MobiBuilder, too.) 

Why?
----
[Projekt Gutenberg-DE](http://gutenberg.spiegel.de) is a project that publishes
German books, whose copyright has expired, on the web; essentially a German
version of [Project Gutenberg](https://www.gutenberg.org). *Unlike* Project
Gutenberg, you can not download their books in any ebook format. And that's why
this package exists.

The `gutenb` script uses getebook to build epub files from books at
Projekt Gutenberg-DE. Note that while the copyright on the books has expired,
the *collection* at Projekt Gutenberg-DE is *not* free of copyright. Only
*private*, *noncommercial* use is allowed for free.

Example
-------
How to get Franz Kafka's "Der Prozess" from Projekt Gutenberg-DE with getebook:

    import getebook.epub
    
    with getebook.epub.EpubBuilder('out.epub') as builder:
        builder.author = 'Franz Kafka'
        builder.title = 'Der Prozess'
        builder.titlepage() # Add a page stating author and title
        # To set up the parser, we hand builder over to it, give it a regex
        # that describes the link to the next page, and tell it which html
        # element holds the book content. In this case, it is a div with id
        # attribute "gutenb".
        p = getebook.EbookParser(builder,
                                 link_next = 'Kapitel [0-9]* >>',
                                 root_tag = 'div',
                                 root_id = 'gutenb'
                                 )
        # Now, you just need to point the parser to the beginning of the
        # book. (We start at page 2 because pate 1 is a titlepage, and
        # builder already made a prettier one.)
        p.getebook('http://gutenberg.spiegel.de', 'buch/der-prozess-157/2')

The output here shouldn't look too bad, but the chapter subtitles are not
centered, even though they are centered in the online version. To fix this, you
need to add some css, like so:

    builer.style_css += '.center {text-align: center;}\n'

Other books require some more tweaks. You can read the gutenb script as a more
extensive example.
