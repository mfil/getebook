# Copyright (c) 2016, Max Fillinger <max@max-fillinger.net>
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

'''Package for downloading ebooks published as webpages and converting
them into an ebook format. Currently, only epub2.0.1 is available as
output format.

Example:

How to get Kafka\'s ``Der Prozess\'\' from gutenberg.spiegel.de

>>> import getebook
>>> import getebook.epub

First, create an ebook builder object and enter the metadata:

>>> builder = getebook.epub.EpubBuilder(\'out.epub\')
>>> builder.title = \'Der Prozess\'
>>> builder.author = \'Franz Kafka\'
>>> builder.lang = \'de\'
>>> builder.titlepage()

Create an EbookParser instance, hand it the builder object, describe the
element holding the book content and what the link to the next page of
the book looks like. Then, just point it to the first page of the book.

>>> p = getebook.EbookParser(builder, link_next=\'Kapitel [0-9]*\',
...                          root_tag=\'div\',
...                          root_class=\'gutenb\')
>>> p.getebook(\'http://gutenberg.spiegel.de\', \'buch/der-prozess-157/2\')
>>> builder.finalize()'''

import html
import html.parser
import re
import requests
import urllib.parse
import warnings

__all__ = ['EbookParser', 'PageNotFound', 'Quirks']

class PageNotFound(Exception):
    pass

class _TOCEntry:
    'Entry in a table of contents.'
    def __init__(self, parent, text, target, entry_no):
        'Initialize the entry.'
        self.text = text
        self.target = target
        self.no = entry_no
        self.parent = parent
        self.entries = [] # Subsections

class TOC:
    'Table of contents.'
    _depth = 1
    _max_depth = 1
    _entry_count = 0

    def __init__(self):
        'Initialize the Toc with an empty list of entries.'
        self.entries = []
        self.new_entries_at = self

    @property
    def depth(self):
        'Depth at which entries are added. (read-only)'
        return self._depth

    @property
    def max_depth(self):
        'Maximal depth of the entries. (read-only)'
        return self._max_depth

    def new_entry(self, text, target):
        'Append a new entry to the TOC at the current depth.'
        if self._depth > self._max_depth:
            self._max_depth = self._depth
        self.new_entries_at.entries.append(_TOCEntry(self.new_entries_at, text,
                                                    target, self._entry_count))
        self._entry_count += 1

    def begin_subsections(self):
        'New entries will be added as subsections to the last entry.'
        self._depth += 1
        self.new_entries_at = self.entries[-1]

    def end_subsections(self):
        'New entries will be added one level higher.'
        self._depth -= 1
        self.new_entries_at = self.new_entries_at.parent

_headings = ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']
_headings_and_p = ['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']
_void_elems = ['area', 'base', 'br', 'col', 'command', 'embed', 'hr', 'img',
              'input']
_html5_only = ['article', 'aside', 'figure', 'footer', 'header', 'nav',
               'section', 'audio', 'source', 'video', 'canvas', 'command',
               'datagrid', 'datalist', 'details', 'output', 'progress', 'rp',
               'rt', 'ruby', 'dialog', 'hgroup', 'mark', 'meter', 'time']

class Element:
    'Represents a html element.'
    _text_len = 0

    def __init__(self, tag, attrs):
        'Initialize an element with no children.'
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []

    def add_child(self, elem):
        'Add a child element (another Element instance or a string).'
        try:
            self._text_len += elem.text_len
        except AttributeError:
            # elem should be a string
            self._text_len += len(elem)
        self.children.append(elem)

    @property
    def text(self):
        'Text inside the element. (read-only attribute)'
        if self.tag == 'br':
            out_str = '\n'
        else:
            out_str = ''
        for child in self.children:
            try:
                out_str += child.text
            except AttributeError:
                # child should be a string
                out_str += child
        return out_str

    @property
    def text_len(self):
        '''Length of the text inside the element. (read-only attribute)
        
        Changing any children (or children\'s children, etc.) after they
        have been added may result in a wrong value.'''
        return self._text_len

class _Pattern:
    '''A _Pattern instance describes a class of html elements. Use the
    match method to compare an element against the description.'''
    def __init__(self, tag, class_val, id_val, text_re, char_lim):
        '''Initialize a _Pattern instance. An element matches if its
        tag, class and id equal tag, class_val and id_val (or, in case
        of lists, are contained therein), and if its text content has
        length <= char_lim and matches text_re. To skip any of these
        checks, set the correspongin argument to None.'''
        if not tag:
            self.tag = None
        elif isinstance(tag, str):
            self.tag = [tag]
        else:
            self.tag = tag
        if not class_val:
            self.cls = None
        elif isinstance(class_val, str):
            self.cls = [class_val]
        else:
            self.cls = class_val
        if not id_val:
            self.id = None
        elif isinstance(id_val, str):
            self.id = [id_val]
        else:
            self.id = id_val
        if text_re:
            self.txt_re = re.compile(text_re)
        else:
            self.txt_re = None
        self.lim = char_lim

    def match_starttag(self, elem):
        'Check if the tag and attributes match the pattern.'
        if self.tag and not elem.tag in self.tag:
            return False
        if self.cls:
            try:
                if not elem.attrs['class'] in self.cls:
                    return False
            except KeyError:
                return False
        if self.id:
            try:
                if not elem.attrs['id'] in self.id:
                    return False
            except KeyError:
                return False
        return True

    def match(self, elem):
        'Check if elem matches the pattern.'
        if self.lim and elem.text_len > self.lim:
            return False
        if not self.match_starttag(elem):
            return False
        if self.txt_re and not self.txt_re.match(elem.text):
            return False
        return True

class Quirks:
    '''A Quirks instance informs the parser about some problems with the
    html code. The following three types of quirks are used:

    - false_heading: heading tags on something that is not a chapter
        heading. In the output, it will not appear in the table of
        contents.
    - par_heading: (part of) a chapter heading is in <p> tags. In the
        output, it will be treated as a heading. If it is immediately
        followed by a proper heading (as in "<p>Chapter N</p>
        <h1>Chapter Title</h1>"), they are joined into one heading.
    - skip: Elements that should not appear in the output.

    If one of the first two quirks applies, a specific function in the
    builder object is called. If the third oen applies, the content is
    simply discarded.
    
    You can add quirks to an EbookParser instance p by calling the
    methods p.quirks.false_heading(), etc.
    '''

    def __init__(self, noscript = True, nohtml5 = True):
        '''Initialize a Quirks instance. If noscript is True (the
        default), all script elements are skipped; if skip_html5 is
        True, all elements that are new in html5 are skipped.'''
        self.false_h = []
        self.par_h = []
        skiptags = []
        if nohtml5:
            skiptags.extend(_html5_only)
        if noscript:
            skiptags.append('script')
        if skiptags:
            self.skip_elem = [_Pattern(skiptags, None, None, None, None)]

    def false_heading(self, class_val, id_val, text_re = None, level = None):
        '''Add conditions for a false heading; class_val and id_val are
        the class and id attribute values for the starttag, text_re is a
        regular expression that is matched against the text and level is
        the heading level. If an argument is set to None, the
        corresponding check will be skipped.'''
        if not level:
            tag = _headings
        elif level >= 1 and level <= 6:
            tag = 'h%d' % level
        else:
            raise ValueError('level must be >= 1 and <= 6')
        self.false_h.append(_Pattern(tag, class_val, id_val, text_re,
                                     char_lim = None))

    def par_heading(self, class_val, id_val, text_re = None, char_lim = 20):
        '''Add conditions for a paragraph heading; class_val, id_val and
        text_re are as in false_heading. To avoid checking long
        paragraphs, char_lim is an upper bound on the length. If a
        paragraph exceeds that length, it is assumed not to be a chapter
        heading. It can be set to None for no limit.'''
        self.par_h.append(_Pattern('p', class_val, id_val, text_re, char_lim))

    def skip(self, tag, class_val, id_val, text_re = None, char_lim = None):
        '''Add conditions for skipping an element. When tag is set to
        \'h*\', it matches any heading. The other arguments are as in
        the other methods.'''
        if tag == 'h*':
            tag = _headings
        self.skip_elem.append(_Pattern(tag, class_val, id_val, text_re,
                                       char_lim))

    def test_false_heading(self, elem):
        'Check if elem is a false heading.'
        if any([p.match(elem) for p in self.false_h]):
            return True
        return False

    def test_par_heading(self, elem):
        'Check if elem is a paragraph heading.'
        if any([p.match(elem) for p in self.par_h]):
            return True
        return False

    def test_skip(self, elem):
        'Check if elem should be skipped.'
        if any([p.match(elem) for p in self.skip_elem]):
            return True
        return False

class EbookParser(html.parser.HTMLParser):
    'Extract ebook content and the URL to the next part.'
    def __init__(self, builder, link_next, root_tag = None, root_class = None,
                 root_id = None):
        '''Initialize the parser. The builder argument should be an
        ebook builder object from a submodule. root_tag, root_class and
        root_id describe the html element that holds ebook content. If
        all of them are None, the whole body is considered to be ebook
        content. link_next is a regular expression to extract the link
        to the next part of the ebook.'''
        super().__init__(convert_charrefs = True)
        if root_tag or root_class or root_id:
            self.root_check = _Pattern(root_tag, root_class, root_id, None,
                                        None)
        else:
            self.root_check = _Pattern('body', None, None, None, None)
        in_content = False
        self.builder = builder
        self.quirks = Quirks()
        self.next_re = re.compile(link_next)
        self.in_anchor = False # Parsing anchor to compare with link_next
        self.next_part = None
        self.elem_stack = []
        self.last_void_tag = None

    def reset(self):
        'Reset this instance.'
        self.next_part = None
        self.in_anchor = False
        self.in_content = False
        self.elem_stack = []
        try:
            del self.block
        except AttributeError:
            pass
        super().reset()

    def handle_starttag(self, tag, attrs):
        '''Handle a start tag. This method is supposed to only be used
        internally.'''
        if self.in_content or self.in_anchor:
            if tag in _headings_and_p:
                # We add a new heading or paragraph tag. Check if there
                # is a previous unclosed heading or paragraph tag.
                for i in range(len(self.elem_stack)-1, -1, -1):
                    if self.elem_stack[i].tag in _headings_and_p:
                        # The ith-to-last element in the stack is an
                        # unclosed paragraph or heading, so we call
                        # _close_elem() i+1 times.
                        for j in range(i+1):
                            unclosed = self._close_elem()
                            warnings.warn('missing </%s> tag in line %d' % \
                                                  (unclosed, self.getpos()[0]))
                        break
            self.elem_stack.append(Element(tag, attrs))
            if tag in _void_elems:
                # Since the new element can't contain any children, we
                # call _close_elem() immediately. We save the starttag
                # since a corresponding endtag may follow.
                self.last_void_tag = self._close_elem()
        elif tag == 'a' and self.next_re and not self.next_part:
            self.in_anchor = True
            self.elem_stack.append(Element(tag, attrs))
        elif tag == 'base':
            for (key, val) in attrs:
                if key == 'href':
                    self.base = val
                    break
        else:
            elem = Element(tag, attrs)
            if self.root_check.match_starttag(elem):
                self.in_content = True

    def handle_endtag(self, tag):
        '''Handle an end tag. This method is supposed to only be used
        internally.'''
        # If this tag closes a void element, we don't need to do
        # anything here (other than set last_void_tag to None).
        if not tag == self.last_void_tag:
            prev_tag = self._close_elem()
            while (self.in_content or self.in_anchor) and prev_tag != tag:
                warnings.warn('missing </%s> tag in line %d' % \
                                                  (prev_tag, self.getpos()[0]))
                prev_tag = self._close_elem()
        self.last_void_tag = None

    def _close_elem(self):
        'Closes the last element on elem_stack and returns its tag.'
        try:
            elem = self.elem_stack.pop()
        except IndexError:
            # We are outside of the book content or the anchor. Set
            # in_content and in_anchor to False in case we have just now
            # left it.
            self.in_content = False
            self.in_anchor = False
            return None
        if self.in_anchor and elem.tag == 'a':
            if self.next_re.match(elem.text):
                try:
                    self.next_part = elem.attrs['href']
                except KeyError:
                    warnings.warn(('The anchor matching link_next has no href '
                                   'attribute.'))
                self.in_anchor = False
        if self.in_content and not self.quirks.test_skip(elem):
            try:
                self.elem_stack[-1].add_child(elem)
            except IndexError:
                # We closed the last element on the stack, now we hand
                # it to the ebook builder.
                if elem.tag == 'p' and self.quirks.test_par_heading(elem):
                    self.builder.par_heading(elem)
                elif elem.tag == 'h' and self.quirks.test_false_h(elem):
                    self.builder.false_heading(elem)
                else:
                    self.builder.handle_elem(elem)
        return elem.tag


    def handle_data(self, data):
        '''Handle data. This method is supposed to only be used internally.'''
        strp_lines = [l.strip() for l in data.splitlines() if len(l) > 0 \
                                                           and not l.isspace()]
        text = '\n'.join(strp_lines)
        if text:
            try:
                self.elem_stack[-1].add_child(text)
            except IndexError:
                if self.in_content:
                    self.builder.handle_elem(text)

    def getebook(self, base, path):
        '''Parse the html from base+path, and keep following the link to
        the next part of the book.'''
        while path:
            try:
                base = self.base
            except AttributeError:
                pass
            r = requests.get(urllib.parse.urljoin(base, path))
            if not r:
                raise PageNotFound('Got error code %03d.' % r.status_code)
            self.builder.new_part()
            self.feed(r.text)
            path = self.next_part
            self.reset()
