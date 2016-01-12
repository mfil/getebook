# Copyright (c) 2015, Max Fillinger <max@max-fillinger.net>
# 
# Permission to use, copy, modify, and/or distribute this software for any
# purpose with or without fee is hereby granted, provided that the above
# copyright notice and this permission notice appear in all copies.
# 
# THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
# REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY
# AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
# INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
# LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR
# OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
# PERFORMANCE OF THIS SOFTWARE.

# The epub format specification is available at http://idpf.org/epub/201

'''Contains the EpubBuilder class to build epub2.0.1 files with the getebook
module.'''

import html
import re
import datetime
import getebook
import os.path
import re
import zipfile

__all__ = ['EpubBuilder', 'EpubTOC', 'Author']

def _normalize(name):
    '''Transform "Firstname [Middlenames] Lastname" into
    "Lastname, Firstname [Middlenames]".'''
    split = name.split()
    if len(split) == 1:
        return name
    return split[-1] + ', ' + ' '.join(name[0:-1])

def _make_starttag(tag, attrs):
    'Write a starttag.'
    out = '<' + tag
    for key in attrs:
        out += ' {}="{}"'.format(key, html.escape(attrs[key]))
    out += '>'
    return out

def _make_xml_elem(tag, text, attr = []):
    'Write a flat xml element.'
    out = '    <' + tag
    for (key, val) in attr:
        out += ' {}="{}"'.format(key, val)
    if text:
        out += '>{}</{}>\n'.format(text, tag)
    else:
        out += ' />\n'
    return out

class EpubTOC(getebook.TOC):
    'Table of contents.'
    _head = ((
      '<?xml version="1.0" encoding="UTF-8"?>\n'
      '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1" xml:lang="en-US">\n'
      '  <head>\n'
      '    <meta name="dtb:uid" content="{}" />\n'
      '    <meta name="dtb:depth" content="{}" />\n'
      '    <meta name="dtb:totalPageCount" content="0" />\n'
      '    <meta name="dtb:maxPageNumber" content="0" />\n'
      '  </head>\n'
      '  <docTitle>\n'
      '    <text>{}</text>\n'
      '  </docTitle>\n'
    ))
    _doc_author = ((
      '  <docAuthor>\n'
      '    <text>{}</text>\n'
      '  </docAuthor>\n'
    ))
    _navp = ((
      '{0}<navPoint id="nav{1}">\n'
      '{0}  <navLabel>\n'
      '{0}    <text>{2}</text>\n'
      '{0}  </navLabel>\n'
      '{0}  <content src="{3}" />\n'
    ))

    def _navp_xml(self, entry, indent_lvl):
        'Write xml for an entry and all its subentries.'
        xml = self._navp.format('  '*indent_lvl, str(entry.no), entry.text,
          entry.target)
        for sub in entry.entries:
            xml += self._navp_xml(sub, indent_lvl+1)
        xml += '  '*indent_lvl + '</navPoint>\n'
        return xml

    def write_xml(self, uid, title, authors):
        'Write the xml code for the table of contents.'
        xml = self._head.format(uid, self.max_depth, title)
        for aut in authors:
            xml += self._doc_author.format(aut)
        xml += '  <navMap>\n'
        for entry in self.entries:
            xml += self._navp_xml(entry, 2)
        xml += '  </navMap>\n</ncx>'
        return xml

class _Fileinfo:
    'Information about a component file of an epub.'
    def __init__(self, name, in_spine = True, guide_title = None,
                 guide_type = None):
        '''Initialize the object. If the file does not belong in the
        reading order, in_spine should be set to False. If it should
        appear in the guide, set guide_title and guide_type.'''
        self.name = name
        (self.ident, ext) = os.path.splitext(name)
        name_split = name.rsplit('.', 1)
        self.ident = name_split[0]
        self.in_spine = in_spine
        self.guide_title = guide_title
        self.guide_type = guide_type
        # Infer media-type from file extension
        ext = ext.lower()
        if ext in ('.htm', '.html', '.xhtml'):
            self.media_type = 'application/xhtml+xml'
        elif ext in ('.png', '.gif', '.jpeg'):
            self.media_type = 'image/' + ext
        elif ext == '.jpg':
            self.media_type = 'image/jpeg'
        elif ext == '.css':
            self.media_type = 'text/css'
        elif ext == '.ncx':
            self.media_type = 'application/x-dtbncx+xml'
        else:
            raise ValueError('Can\'t infer media-type from extension: %s' % ext)
    def manifest_entry(self):
        'Write the XML element for the manifest.'
        return _make_xml_elem('item', '',
          [
            ('href', self.name),
            ('id', self.ident),
            ('media-type', self.media_type)
          ])
    def spine_entry(self):
        '''Write the XML element for the spine.
        (Empty string if in_spine is False.)'''
        if self.in_spine:
            return _make_xml_elem('itemref', '', [('idref', self.ident)])
        else:
            return ''
    def guide_entry(self):
        '''Write the XML element for the guide.
        (Empty string if no guide title and type are given.)'''
        if self.guide_title and self.guide_type:
            return _make_xml_elem('reference', '',
              [
                ('title', self.guide_title),
                ('type', self.guide_type),
                ('href', self.name)
              ])
        else:
            return ''

class _EpubMeta:
    'Metadata entry for an epub file.'
    def __init__(self, tag, text, *args):
        '''The metadata entry is an XML element. *args is used for
        supplying the XML element's attributes as (key, value) pairs.'''
        self.tag = tag
        self.text = text
        self.attr = args
    def write_xml(self):
        'Write the XML element.'
        return _make_xml_elem(self.tag, self.text, self.attr)
    def __repr__(self):
        'Returns the text.'
        return self.text
    def __str__(self):
        'Returns the text.'
        return self.text

class _EpubDate(_EpubMeta):
    'Metadata element for the publication date.'
    _date_re = re.compile('^([0-9]{4})(-[0-9]{2}(-[0-9]{2})?)?$')
    def __init__(self, date):
        '''date must be a string of the form "YYYY[-MM[-DD]]". If it is
        not of this form, or if the date is invalid, ValueError is
        raised.'''
        m = self._date_re.match(date) 
        if not m:
            raise ValueError('invalid date format')
        year = int(m.group(1))
        try:
            mon = int(m.group(2)[1:])
            if mon < 0 or mon > 12:
                raise ValueError('month must be in 1..12')
        except IndexError:
            pass
        try:
            day = int(m.group(3)[1:])
            datetime.date(year, mon, day) # raises ValueError if invalid
        except IndexError:
            pass
        self.tag = 'dc:date'
        self.text = date
        self.attr = ()

class _EpubLang(_EpubMeta):
    'Metadata element for the language of the book.'
    _lang_re = re.compile('^[a-z]{2}(-[A-Z]{2})?$')
    def __init__(self, lang):
        '''lang must be a lower-case two-letter language code,
        optionally followed by a "-" and a upper-case two-letter country
        code. (e.g., "en", "en-US", "en-UK", "de", "de-DE", "de-AT")'''
        if self._lang_re.match(lang):
            self.tag = 'dc:language'
            self.text = lang
            self.attr = ()
        else:
            raise ValueError('invalid language format')

class Author(_EpubMeta):
    '''To control the file-as and role attribute for the authors, pass
    an Author object to the EpubBuilder instead of a string. The file-as
    attribute is a form of the name used for sorting. The role attribute
    describes how the person was involved in the work.

    You ONLY need this if an author's name is not of the form
    "Given-name Family-name", or if you want to specify a role other
    than author. Otherwise, you can just pass a string.

    The value of role should be a MARC relator, e.g., "aut" for author
    or "edt" for editor. See http://www.loc.gov/marc/relators/ for a
    full list.'''
    def __init__(self, name, fileas = None, role = 'aut'):
        '''Initialize the object. If the argument "fileas" is not given,
        "Last-name, First-name" is used for the file-as attribute. If
        the argument "role" is not given, "aut" is used for the role
        attribute.'''
        if not fileas:
            fileas = _normalize(name)
        self.tag = 'dc:creator'
        self.text = name
        self.attr = (('opf:file-as', fileas), ('opf:role', role))

class _OPFfile:
    '''Class for writing the OPF (Open Packaging Format) file for an
    epub file. The OPF file contains the metadata, a manifest of all
    component files in the epub, a "spine" which specifies the reading
    order and a guide which points to important components of the book
    such as the title page.'''

    _opf = (
      '<?xml version="1.0" encoding="UTF-8"?>\n'
      '<package version="2.0" xmlns="http://www.idpf.org/2007/opf" unique_identifier="uid_id">\n'
      '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">\n'
      '{}'
      '  </metadata>\n'
      '  <manifest>\n'
      '{}'
      '  </manifest>\n'
      '  <spine toc="toc">\n'
      '{}'
      '  </spine>\n'
      '  <guide>\n'
      '{}'
      '  </guide>\n'
      '</package>\n'
    )
    def __init__(self):
        'Initialize.'
        self.meta = []
        self.filelist = []
    def write_xml(self):
        'Write the XML code for the OPF file.'
        metadata = ''
        for elem in self.meta:
            metadata += elem.write_xml()
        manif = ''
        spine = ''
        guide = ''
        for finfo in self.filelist:
            manif += finfo.manifest_entry()
            spine += finfo.spine_entry()
            guide += finfo.guide_entry()
        return self._opf.format(metadata, manif, spine, guide)

class EpubBuilder:
    '''Builds an epub2.0.1 file. Some of the attributes of this class
    (title, uid, lang) are marked as "mandatory" because they represent
    metadata that is required by the epub specification. If these
    attributes are left unset, default values will be used.'''

    _style_css = (
      'h1, h2, h3, h4, h5, h6 {\n'
      '  text-align: center;\n'
      '}\n'
      'p {\n'
      '  text-align: justify;\n'
      '  margin-top: 0.125em;\n'
      '  margin-bottom: 0em;\n'
      '  text-indent: 1.0em;\n'
      '}\n'
      '.getebook-tp {\n'
      '  margin-top: 8em;\n'
      '}\n'
      '.getebook-tp-authors {\n'
      '  font-size: 2em;\n'
      '  text-align: center;\n'
      '  margin-bottom: 1em;\n'
      '}\n'
      '.getebook-tp-title {\n'
      '  font-weight: bold;\n'
      '  font-size: 3em;\n'
      '  text-align: center;\n'
      '}\n'
      '.getebook-tp-sub {\n'
      '  text-align: center;\n'
      '  font-weight: normal;\n'
      '  font-size: 0.8em;\n'
      '  margin-top: 1em;\n'
      '}\n'
      '.getebook-false-h {\n'
      '  font-weight: bold;\n'
      '  font-size: 1.5em;\n'
      '}\n'
      '.getebook-small-h {\n'
      '  font-style: normal;\n'
      '  font-weight: normal;\n'
      '  font-size: 0.8em;\n'
      '}\n'
    )

    _container_xml = (
      '<?xml version="1.0"?>\n'
      '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
      '  <rootfiles>\n'
      '    <rootfile full-path="package.opf" media-type="application/oebps-package+xml"/>\n'
      '  </rootfiles>\n'
      '</container>\n'
    )

    _html = (
      '<?xml version="1.0" encoding="utf-8"?>\n'
      '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">\n'
      '<html xmlns="http://www.w3.org/1999/xhtml">\n'
      '  <head>\n'
      '    <title>{}</title>\n'
      '    <meta http-equiv="content-type" content="application/xtml+xml; charset=utf-8" />\n'
      '    <link href="style.css" rel="stylesheet" type="text/css" />\n'
      '  </head>\n'
      '  <body>\n{}'
      '  </body>\n'
      '</html>\n'
    )

    _finalized = False

    def __init__(self, epub_file):
        '''Initialize the EpubBuilder instance. "epub_file" is the
        filename of the epub to be created.'''
        self.epub_f = zipfile.ZipFile(epub_file, 'w', zipfile.ZIP_DEFLATED)
        self.epub_f.writestr('mimetype', 'application/epub+zip')
        self.epub_f.writestr('META-INF/container.xml', self._container_xml)
        self.toc = EpubTOC()
        self.opf = _OPFfile()
        self.opf.filelist.append(_Fileinfo('toc.ncx', False))
        self.opf.filelist.append(_Fileinfo('style.css', False))
        self._authors = []
        self.opt_meta = {} # Optional metadata (other than authors)
        self.content = ''
        self.part_no = 0
        self.cont_filename = 'part%03d.html' % self.part_no

    def __enter__(self):
        'Return self for use in with ... as ... statement.'
        return self

    def __exit__(self, except_type, except_val, traceback):
        'Call finalize() and close the file.'
        try:
            self.finalize()
        finally:
            # Close again in case an exception happened in finalize()
            self.epub_f.close()
        return False

    @property
    def uid(self):
        '''Unique identifier of the ebook. (mandatory)

        If this property is left unset, a pseudo-random string will be
        generated which is long enough for collisions with existing
        ebooks to be extremely unlikely.'''
        try:
            return self._uid
        except AttributeError:
            import random
            from string import (ascii_letters, digits)
            alnum = ascii_letters + digits
            self.uid = ''.join([random.choice(alnum) for i in range(15)])
            return self._uid
    @uid.setter
    def uid(self, val):
        self._uid = _EpubMeta('dc:identifier', str(val), ('id', 'uid_id'))

    @property
    def title(self):
        '''Title of the ebook. (mandatory)

        If this property is left unset, it defaults to "Untitled".'''
        try:
            return self._title
        except AttributeError:
            self.title = 'Untitled'
            return self._title
    @title.setter
    def title(self, val):
        # If val is not a string, raise TypeError now rather than later.
        self._title = _EpubMeta('dc:title', '' + val)

    @property
    def lang(self):
        '''Language of the ebook. (mandatory)

        The language must be given as a lower-case two-letter code, optionally
        followed by a "-" and an upper-case two-letter country code.
        (e.g., "en", "en-US", "en-UK", "de", "de-DE", "de-AT")

        If this property is left unset, it defaults to "en".'''
        try:
            return self._lang
        except AttributeError:
            self.lang = 'en'
            return self._lang
    @lang.setter
    def lang(self, val):
        self._lang = _EpubLang(val)

    @property
    def author(self):
        '''Name of the author. (optional)
        
        If there are multiple authors, pass a list of strings.

        To control the file-as and role attribute, use author objects instead
        of strings; file-as is an alternate form of the name used for sorting.
        For a description of the role attribute, see the docstring of the
        author class.'''
        if len(self._authors) == 1:
            return self._authors[0]
        return tuple([aut for aut in self._authors])
    @author.setter
    def author(self, val):
        if isinstance(val, Author) or isinstance(val, str):
            authors = [val]
        else:
            authors = val
        for aut in authors:
            try:
                self._authors.append(Author('' + aut))
            except TypeError:
                # aut is not a string, so it should be an Author object
                self._authors.append(aut)
    @author.deleter
    def author(self):
        self._authors = []

    @property
    def date(self):
        '''Publication date. (optional)
        
        Must be given in "YYYY[-MM[-DD]]" format.'''
        try:
            return self.opt_meta['date']
        except KeyError:
            return None
    @date.setter
    def date(self, val):
        self.opt_meta['date'] = _EpubDate(val)
    @date.deleter
    def date(self):
        del self._date

    @property
    def rights(self):
        'Copyright/licensing information. (optional)'
        try:
            return self.opt_meta['rights']
        except KeyError:
            return None
    @rights.setter
    def rights(self, val):
        self.opt_meta['rights'] = _EpubMeta('dc:rights', '' + val)
    @rights.deleter
    def rights(self):
        del self._rights

    @property
    def publisher(self):
        'Publisher name. (optional)'
        try:
            return self.opt_meta['publisher']
        except KeyError:
            return None
    @publisher.setter
    def publisher(self, val):
        self.opt_meta['publisher'] = _EpubMeta('dc:publisher', '' + val)
    @publisher.deleter
    def publisher(self):
        del self._publisher
    
    @property
    def style_css(self):
        '''CSS stylesheet for the files that are generated by the EpubBuilder
        instance. Can be overwritten or extended, but not deleted.'''
        return self._style_css
    @style_css.setter
    def style_css(self, val):
        self._style_css = '' + val

    def titlepage(self, main_title = None, subtitle = None):
        '''Create a title page for the ebook. If no main_title is given,
        the title attribute of the EpubBuilder instance is used.'''
        tp = '<div class="getebook-tp">\n'
        if len(self._authors) >= 1:
            if len(self._authors) == 1:
                aut_str = str(self._authors[0])
            else:
                aut_str = ', '.join(str(self._authors[0:-1])) + ', and ' \
                                                       + str(self._authors[-1])
            tp += '<div class="getebook-tp-authors">%s</div>\n' % aut_str
        if not main_title:
            main_title = str(self.title)
        tp += '<div class="getebook-tp-title">%s' % main_title
        if subtitle:
            tp += '<div class="getebook-tp-sub">%s</div>' % subtitle
        tp += '</div>\n</div>\n'
        self.opf.filelist.insert(0, _Fileinfo('title.html',
          guide_title = 'Titlepage', guide_type = 'title-page'))
        self.epub_f.writestr('title.html', self._html.format(self.title, tp))

    def headingpage(self, heading, subtitle = None, toc_text = None):
        '''Create a page containing only a (large) heading, optionally
        with a smaller subtitle. If toc_text is not given, it defaults
        to the heading.'''
        self.new_part()
        tag = 'h%d' % min(6, self.toc.depth)
        self.content += '<div class="getebook-tp">'
        self.content += '<{} class="getebook-tp-title">{}'.format(tag, heading)
        if subtitle:
            self.content += '<div class="getebook-tp-sub">%s</div>' % subtitle
        self.content += '</%s>\n' % tag
        if not toc_text:
            toc_text = heading
        self.toc.new_entry(toc_text, self.cont_filename)
        self.new_part()

    def insert_file(self, name, in_spine = False, guide_title = None,
      guide_type = None, arcname = None):
        '''Include an external file into the ebook. By default, it will
        be added to the archive under its basename; the argument
        "arcname" can be used to specify a different name.'''
        if not arcname:
            arcname = os.path.basename(name)
        self.opf.filelist.append(_Fileinfo(arcname, in_spine, guide_title,
                                 guide_type))
        self.epub_f.write(name, arcname)

    def add_file(self, arcname, str_or_bytes, in_spine = False,
      guide_title = None, guide_type = None):
        '''Add the string or bytes instance str_or_bytes to the archive
        under the name arcname.'''
        self.opf.filelist.append(_Fileinfo(arcname, in_spine, guide_title,
                                 guide_type))
        self.epub_f.writestr(arcname, str_or_bytes)

    def false_heading(self, elem):
        '''Handle a "false heading", i.e., text that appears in heading
        tags in the source even though it is not a chapter heading.'''
        elem.attrs['class'] = 'getebook-false-h'
        elem.tag = 'p'
        self.handle_elem(elem)

    def _heading(self, elem):
        '''Write a heading.'''
        # Handle paragraph heading if we have one waiting (see the
        # par_heading method). We don\'t use _handle_par_h here because
        # we merge it with the subsequent proper heading.
        try:
            par_h = self.par_h
            del self.par_h
        except AttributeError:
            toc_text = elem.text
        else:
            # There is a waiting paragraph heading, we merge it with the
            # new heading.
            toc_text = par_h.text + '. ' + elem.text
            par_h.tag = 'div'
            par_h.attrs['class'] = 'getebook-small-h'
            elem.children.insert(0, par_h)
        # Set the class attribute value.
        elem.attrs['class'] = 'getebook-chapter-h'
        self.toc.new_entry(toc_text, self.cont_filename)
        # Add heading to the epub.
        tag = 'h%d' % min(self.toc.depth, 6)
        self.content += _make_starttag(tag, elem.attrs)
        for elem in elem.children:
            self.handle_elem(elem)
        self.content += '</%s>\n' % tag

    def par_heading(self, elem):
        '''Handle a "paragraph heading", i.e., a chaper heading or part
        of a chapter heading inside paragraph tags. If it is immediately
        followed by a heading, they will be merged into one.'''
        self.par_h = elem

    def _handle_par_h(self):
        'Check if there is a waiting paragraph heading and handle it.'
        try:
            self._heading(self.par_h)
        except AttributeError:
            pass

    def handle_elem(self, elem):
        'Handle html element as supplied by getebook.EbookParser.'
        try:
            tag = elem.tag
        except AttributeError:
            # elem should be a string
            is_string = True
            tag = None
        else:
            is_string = False
        if tag in getebook._headings:
            self._heading(elem)
        else:
            # Handle waiting par_h if necessary (see par_heading)
            try:
                self._heading(self.par_h)
            except AttributeError:
                pass
            if is_string:
                self.content += elem
            elif tag == 'br':
                self.content += '<br />\n'
            elif tag == 'img':
                self.content += self._handle_image(elem.attrs) + '\n'
            elif tag == 'a' or tag == 'noscript':
                # Ignore tag, just write child elements
                for child in elem.children:
                    self.handle_elem(child)
            else:
                self.content += _make_starttag(tag, elem.attrs)
                for child in elem.children:
                    self.handle_elem(child)
                self.content += '</%s>' % tag
                if tag == 'p':
                    self.content += '\n'

    def _handle_image(self, attrs):
        'Returns the alt text of an image tag.'
        try:
            return attrs['alt']
        except KeyError:
            return ''

    def new_part(self):
        '''Begin a new part of the epub. Write the current html document
        to the archive and begin a new one.'''
        # Handle waiting par_h (see par_heading)
        try:
            self._heading(self.par_h)
        except AttributeError:
            pass
        if self.content:
            html = self._html.format(self.title, self.content)
            self.epub_f.writestr(self.cont_filename, html)
            self.part_no += 1
        self.content = ''
        self.cont_filename = 'part%03d.html' % self.part_no
        self.opf.filelist.append(_Fileinfo(self.cont_filename))

    def finalize(self):
        'Complete and close the epub file.'
        # Handle waiting par_h (see par_heading)
        if self._finalized:
            # Avoid finalizing twice. Otherwise, calling finalize inside
            # a with-block would lead to an exception when __exit__
            # calls finalize again.
            return
        try:
            self._heading(self.par_h)
        except AttributeError:
            pass
        if self.content:
            html = self._html.format(self.title, self.content)
            self.epub_f.writestr(self.cont_filename, html)
        self.opf.meta = [self.uid, self.lang, self.title] + self._authors
        self.opf.meta += self.opt_meta.values()
        self.epub_f.writestr('package.opf', self.opf.write_xml())
        self.epub_f.writestr('toc.ncx',
          self.toc.write_xml(self.uid, self.title, self._authors))
        self.epub_f.writestr('style.css', self._style_css)
        self.epub_f.close()
        self._finalized = True
