#! /usr/bin/python

# -*- coding: utf-8 -*-

# pyqt4topyqt5.py
# Nov. 12 2013
# Author: Vincent Vande Vyvre <vincent.vandevyvre@swing.be>
# Copyright: 2013 Vincent Vande Vyvre
# Licence: LGPL3

import os
import re
import shutil
import argparse
import sys
import tokenize
import subprocess

from datetime import datetime
from codecs import BOM_UTF8, lookup, open as open_

PY_VERS = sys.version_info[0]

if PY_VERS < 3:
    from StringIO import StringIO
    range_ = xrange
else:
    from io import StringIO
    range_ = range

from qtclass import MODULES, CLASSES, DISCARDED

L_SEP = os.linesep
PYEXT = (os.extsep + "py", os.extsep + "pxi")
MOD_RE = {'QtGui': re.compile('(?<=QtGui.)(.*?)(?=[.\(\),])', re.DOTALL),
          'QtWebKit': re.compile('(?<=QtWebKit.)(.*?)(?=[.\(\),])', re.DOTALL)}
SIG_RE = {'fun_re': re.compile('(?<=\()(.*)(?=\))', re.DOTALL),
          'send_re': re.compile('(?<=.connect\()(.*?)(?=[, SIGNAL])', re.DOTALL),
          'sig_re': re.compile('(?<=SIGNAL\(["\' ])(.*?)(?=["\',])', re.DOTALL),
          'call_re': re.compile('(?<=["\']\),)(.*?)(?=[,\)])', re.DOTALL),
          'slot_re': re.compile('(?<=SLOT\(["\'])(.*?)(?=["\'])', re.DOTALL),
          'lamb_re': re.compile('(?<=lambda )(.*?)(?=[\)])', re.DOTALL),
          'pysig_re': re.compile('(?<=["\'])(.*?)(?=["\'])', re.DOTALL),
          'emit_re': re.compile('(?<=SIGNAL\()(.*?)(?=[\)])', re.DOTALL)}
DOT_RE = re.compile('(?<=\()(.*?)(?=.NoDotAndDotDot)')
WHEEL_RE = re.compile('(?<=def wheelEvent\(self,)(.*?)(?=\):)')
LAYOUT_RE = re.compile('(.*?)(\=)(.*?)(?=Layout\()')
DSK_RE = re.compile('(.*?)(\=)(.*?)(?=QDesktopServices\()')
DATE_RE = re.compile('(.*?)(\=)(.*?)(?=QDate\()')
CLS_RE = re.compile('(?<=class )(.*?)(?=[\(:])')

# Utils

def diff_parenthesis(line):
    opened = line.count('(')
    closed = line.count(')')
    return opened - closed


class PyQt4ToPyQt5(object):
    def __init__(self, source, dest, log):
        self.log = log
        self.source = source
        self.dest = dest
        self.indent = ' '
        self.tools = Tools()

        self._has_qtwidget_import = False

    def setup(self):
        self.print_('Processing file: `%s`' % self.source)
        self.modified = {'QtGui': False, 'QtWidgets': False,
                        'QtWebKit': False, 'QtWebKitWidgets': False,
                        'QtMultimedia': False, 'QSound': False,
                        'QtCore': False, 'QtPrintSupport': False,
                        'QStandardPaths': False}
        src = self.tools.get_code_lines(self.source)
        if not src:
            self.print_('  Error: Unable to read the file: %s\n  Reason: %s\n'
                        % (self.source, self.tools.last_error))
            return

        try:
            self.indent = self.get_token_indent(''.join(src))[0]
        except IndexError:
            # Never seen a PyQt4 script without indentation, but ...
            self.indent = ' '

        # src is the list of logical lines code, NOT physical lines
        qt4, gui, web = self.get_import_lines(src)
        if not any([gui, web]):
            if qt4:
                self.finish_process(src)
                return

            self.print_('  No changes needed.\n')
            return

        if gui:
            src = self.change_module_name(src, 'QtGui', 'QtWidgets')
            src = self.change_module_name(src, 'QtGui', 'QtPrintSupport')

        if web:
            src = self.change_module_name(src, 'QtWebKit', 'QtWebKitWidgets')

        src = self.change_import_lines(src)

        self.fix_qfiledialog(src)
        self.fix_qdir(src)
        self.fix_qwidget(src)
        self.fix_qtscript(src)
        self.fix_qtxml(src)
        self.fix_qtdeclarative(src)
        self.fix_qgraphicsitemanimation(src)
        self.fix_qtopengl(src)
        self.fix_emit(src)
        self.fix_connect(src)
        self.fix_disconnect(src)
        self.fix_slot(src)
        self.fix_translations(src)
        self.fix_wheelevent(src)
        self.fix_layoutmargin(src)
        self.fix_qdesktopservices(src)
        self.fix_qdate(src)
        self.fix_qgraphicsitem(src)
        self.fix_qheader(src)
        self.fix_qinputdialog(src)
        self.fix_qchar(src)
        self.replace_classnames(src)
        self.finish_process(src)

    def finish_process(self, src):
        src, fixs = self.clean_file(src)

        self.save_changes(src)
        if fixs:
            if len(fixs) == 1:
                txt = "  FIXME added:\n%s" % fixs[0][:-1]

            else:
                txt = "  FIXMEs added:\n" + ''.join(fixs)[:-1]

            self.print_(txt)
        self.print_('  File updated.\n')

    def get_import_lines(self, lines):
        """Check if changes are needed.

        Args:
        lines -- source code

        Returns:
        (True, True, True) if there's PyQt4 or/and QtGui or/and QtWebkit imports
        """
        qt4 = gui = web = False
        for line in lines:
            if 'from PyQt4' in line:
                qt4 = True
                if '.Qt' in line:
                    gui = True
                    web = True
                if 'QtGui' in line:
                    gui = True
                if 'QtWebKit' in line:
                    web = True
                if all([gui, web]):
                    break

        return qt4, gui, web

    def change_module_name(self, lines, old_mod, new_mod):
        """Change the module name for the class wich are moved to a new module.

        Args:
        lines -- source code
        old_mod -- the old name of the module
        new_mod -- the name of the module where the class has been moved
        """
        fixme = "# FIXME$ Ambiguous syntax, can't refactor it\n"
        classes = CLASSES[new_mod]
        news = []
        count = 0
        def get_module_name(widget):
            if widget == 'QSound':
                self.modified['QtMultimedia'] = True
                self.modified['QSound'] = True
                return 'QtMultimedia'

            if widget == 'QStringListModel':
                self.modified['QtCore'] = True
                return 'QtCore'

            if widget in classes:
                self.modified[new_mod] = True
                return new_mod

            self.modified[old_mod] = True
            return old_mod

        while count < len(lines):
            line = lines[count]
            if not self.is_code_line(line) or ' import ' in line:
                news.append(line)
                count += 1
                continue

            if old_mod in line:
                line = line.replace('.qApp', '.QApplication.instance()')
                names = MOD_RE[old_mod].findall(line)
                if not names:
                    news.append(line)
                    count += 1
                    continue

                new = []
                parts = line.split(old_mod)
                if line.startswith(old_mod):
                    new.append(get_module_name(names.pop(0).strip()))

                for part in parts[:-1]:
                    if not part:
                        continue

                    new.append(part)
                    try:
                        new.append(get_module_name(names.pop(0).strip()))
                    except IndexError:
                        indent = self.get_token_indent(line)
                        news.append("%s%s" %(indent, fixme))
                        news.append(line)
                        count += 2
                        continue

                new.append(parts[-1])
                ln = ''.join(new)
                news.append(ln)
                count += 1

            else:
                news.append(lines[count])
                count += 1

        return news

    def fix_qfiledialog(self, lines):
        """Change the name of the class QFileDialog.

        Args:
        lines -- source code
        """
        olds = ('getOpenFileNamesAndFilter', 'getOpenFileNameAndFilter',
                    'getSaveFileNameAndFilter')
        news = ('getOpenFileNames', 'getOpenFileName', 'getSaveFileName')
        count = 0
        while count < len(lines):
            if not self.is_code_line(lines[count]):
                pass

            elif 'AndFilter' in lines[count]:
                for old in olds:
                    if old in lines[count]:
                        lines[count] = lines[count].replace('AndFilter', '')
                        break

            elif 'FileName' in lines[count]:
                for new in news:
                    if new in lines[count]:
                        line = lines[count].rstrip()
                        if self.count_ref(line.split('=')[0]) == 2:
                            continue

                        # Since the old method returns a str and the new one
                        # returns a tuple, we insert an indice [0] into the
                        # final parenthesis
                        _, end = self.find_closing_parenthesis(line, new)
                        lines[count] = ''.join([line[:end+1], '[0]',
                                                    line[end+1:], '\n'])

                        break

            count += 1

    def fix_qdir(self, lines):
        """Replace QDir filter NoDotAndDotDot by filters NoDot and NoDotDot
        and convertSeparators() by toNativeSeparators()

        Args:
        lines -- source code
        """
        for idx, line in enumerate(lines):
            if self.is_code_line(line):
                if '.NoDotAndDotDot' in line:
                    inst = DOT_RE.search(line.lstrip())
                    if inst is not None:
                        name = inst.group(0).split('|')[-1].lstrip()
                        rep = '.NoDot | %s.NoDotDot' % name
                        lines[idx]= line.replace('.NoDotAndDotDot', rep)

                if '.convertSeparators(' in line:
                    lines[idx]= line.replace('convertSeparators',
                                                'toNativeSeparators')

    def fix_qwidget(self, lines):
        """
        Checks if some QWidget classes are used without importing the QWidget module

        This function is SLOW
        """

        def import_qwidgets():
            i = 0
            self._has_qtwidget_import = True

            while i < len(lines):
                l = lines[i]

                if self.is_code_line(l) and 'import' in l and not '__future__' in l:
                    indent = self.get_token_indent(l)
                    lines.insert(i+1, indent + 'from PyQt5.QtWidgets import *\n')
                    return

                i += 1

        if self._has_qtwidget_import:
            return

        count = 0
        while count < len(lines):
            line = lines[count]

            if self.is_code_line(line):
                for w in CLASSES['QtWidgets']:
                    if w in line:
                        import_qwidgets()
                        return

            count += 1

    def fix_qtscript(self, lines):
        """Insert a FIXME for the class QtScript and QtScriptTools.

        Args:
        lines -- source code
        """
        fixme = '# FIXME$ QtScript and QtScriptTools are no longer supported.\n'
        count = 0
        while count < len(lines):
            line = lines[count]
            if self.is_code_line(line):
                if 'QtScript' in line or 'QScript' in line:
                    indent = self.get_token_indent(line)
                    lines.insert(count, '%s%s' %(indent, fixme))
                    count += 1
            count += 1

    def fix_qtxml(self, lines):
        """Insert a FIXME for the classes QXMLStreamReader and QXMLStreamWriter.

        Args:
        lines -- source code
        """
        fixme = '# FIXME$ QtXml is no longer supported.\n'
        count = 0
        while count < len(lines):
            line = lines[count]
            if self.is_code_line(line):
                if 'QtXml' in line:
                    indent = self.get_token_indent(line)
                    lines.insert(count, '%s%s' %(indent, fixme))
                    count += 1
            count += 1

    def fix_qtdeclarative(self, lines):
        """Insert a FIXME for the class QtDeclarative.

        Args:
        lines -- source code
        """
        fixme = '# FIXME$ QtDeclarative module is no longer supported.\n'
        names = ['QtDeclarative', 'QDeclarative', 'QPyDeclarative']
        count = 0
        while count < len(lines):
            line = lines[count]
            if self.is_code_line(line):
                for name in names:
                    if name in line:
                        indent = self.get_token_indent(line)
                        lines.insert(count, '%s%s' %(indent, fixme))
                        count += 1
                        break
            count += 1

    def fix_qgraphicsitemanimation(self, lines):
        """Insert a FIXME for the class QGraphicsItemAnimation

        Args:
        lines -- source code
        """
        fixme = '# FIXME$ QGraphicsItemAnimation class is no longer supported.\n'
        count = 0
        while count < len(lines):
            line = lines[count]
            if self.is_code_line(line):
                if 'QGraphicsItemAnimation' in line:
                    indent = self.get_token_indent(line)
                    lines.insert(count, '%s%s' %(indent, fixme))
                    count += 1
            count += 1

    def fix_qtopengl(self, lines):
        """Insert a FIXME for the module QtOpenGl

        Args:
        lines -- source code
        """
        fixme = '# FIXME$ Only QGLContext, QGLFormat and QGLWidget are supported.\n'
        classes = DISCARDED['QtOpenGl']
        count = 0
        while count < len(lines):
            line = lines[count]
            if self.is_code_line(line):
                if 'QGL' in line:
                    for cls in classes:
                        if cls in line:
                            indent = self.get_token_indent(line)
                            lines.insert(count, '%s%s' %(indent, fixme))
                            count += 1
                            break
            count += 1

    def split_function(self, function):
        slices = ['']
        current = 0
        i = 0
        while i < len(function):
            if function[i] == ',':
                slices.append('')
                current += 1
                i += 1
                continue

            slices[current] += function[i]

            if function[i] == '(':
                inside = 1
                while inside != 0:
                    i += 1

                    if function[i] == '(':
                        inside += 1
                    elif function[i] == ')':
                        inside -= 1

                    slices[current] += function[i]
            i += 1

        result = []
        i = 0
        while i < len(slices):
            if 'lambda ' in slices[i]:
                lambda_f = ''

                while i < len(slices):
                    if lambda_f == '':
                        lambda_f += slices[i]
                    else:
                        lambda_f += ',' + slices[i]

                    if ':' in slices[i]:
                        break

                    i += 1

                result.append(lambda_f)
            else:
                result.append(slices[i])

            i += 1

        if len(result) == 1 and result[0].strip() == '':
            return []

        return [s.strip() for s in result]

    def remove_signal_slot(self, el):
        if "SIGNAL" in el or "SLOT" in el:
            content = SIG_RE['fun_re'].search(el).groups()[0]
            content = content.strip('\'').strip('"')

            slices = content.split('(')
            if len(slices) == 1:
                return slices

            return [slices[0]] + self.split_function(slices[1].replace(')', '').replace('const ', '').replace('&', '').replace('*', ''))
        return [el]

    def fix_connect(self, lines):
        for idx, line in enumerate(lines):
            if not self.is_code_line(line) or not 'connect(' in line:
                continue

            function = SIG_RE['fun_re'].search(line)
            if function is not None:
                arguments = self.split_function(function.groups()[0])
                if len(arguments) < 2:
                    return
                signal = self.remove_signal_slot(arguments[1])
                signal = [s.replace('QString', '\'QString\'') for s in signal]
                indent = self.get_token_indent(line)
                if len(signal) == 1:
                    lines[idx] = indent + '%s.%s.connect(%s)\n' % (arguments[0], signal[0], ", ".join([self.remove_signal_slot(e)[0] for e in arguments[2:]]))
                    continue
                lines[idx] = indent + '%s.%s[%s].connect(%s)\n' % (arguments[0], signal[0], ", ".join(signal[1:]), ','.join([self.remove_signal_slot(e)[0] for e in arguments[2:]]))

    def fix_disconnect(self, lines):
        """Refactor the pyqtSignal.disconnect()

        Args:
        lines -- source code
        """
        count = 0
        while count < len(lines):
            line = lines[count]
            if self.is_code_line(line):
                if '.disconnect(' in line and 'SIGNAL' in line:
                    indent = self.get_token_indent(line)
                    line = line.lstrip()
                    if line.startswith(('QObject.disconnect(',
                                        'QtCore.QObject.disconnect(')):
                        parts = line.split('(')
                        obj = parts[1].split(',')[0]
                        lines[count] = '%s%s(%s)\n' %(indent, parts[0], obj)

                    else:
                        obj = line.split('(')[0]
                        lines[count] = '%s%s()\n' %(indent, obj)
            count += 1

    def fix_slot(self, lines):
        """
        pyqtSignature decorator changed into pyqtSlot
        """
        for idx, line in enumerate(lines):
            lines[idx] = line.replace('@pyqtSignature', '@pyqtSlot')

    def fix_emit(self, lines):
        """
        Refactor the pyqtSignal.emit() old-style into a new-style line.
        Attempts also to create unexisting signals

        Args:
        lines -- the list of source code lines
        """

        def create_signal(currentIdx, name, argnumbers):
            l = lines[currentIdx]
            while not 'class ' in l:
                currentIdx -= 1
                l = lines[currentIdx]
            currentIdx += 1
            l = lines[currentIdx]
            while True:
                if self.is_code_line(l) and name in l:
                    return
                if self.is_code_line(l) and not 'pyqtSignal' in l:
                    break
                currentIdx += 1
                l = lines[currentIdx]

            indent = self.get_token_indent(l)
            lines.insert(currentIdx, "%s = pyqtSignal(%s)\n" % (indent + name, ','.join(['QVariant' for i in range(argnumbers)])))

        fixme = "# FIXME$ Ambiguous syntax for this signal, can't refactor it.\n"
        count = 0
        while count < len(lines):
            line = lines[count]
            if self.is_code_line(line) and '.emit(' in line and 'SIGNAL(' in line:
                parts = line.split('.emit')
                function = SIG_RE['fun_re'].search(parts[1])
                if function is not None:
                    args = self.split_function(function.groups()[0])
                    diff = diff_parenthesis(args[-1])
                    parenthesis = ''.join([')' for i in range(abs(diff))])
                    if diff < 0:
                        li = args[-1].rsplit(')', abs(diff))
                        args[-1] = ''.join(li)

                    lines[count] = '%s.%s.emit(%s)%s\n' % (parts[0], self.remove_signal_slot(args[0])[0], ', '.join(args[1:]), parenthesis)
                    create_signal(count, self.remove_signal_slot(args[0])[0], len(args)-1)
            count += 1

    def fix_translations(self, lines):
        """Fix the translation syntax.

        Args:
        lines -- the list of source code lines
        """
        count = 0
        while count < len(lines):
            line = lines[count]
            if self.is_code_line(line):
                if '.translate' in line:
                    ln = ''
                    parts = line.split('.translate')
                    for part in parts:
                        if part.endswith('QApplication'):
                            # QtGui has been already changed to QtWidgets
                            if part.endswith('QtWidgets.QApplication'):
                                ln += part[:-22] + 'QtCore.QCoreApplication'

                            else:
                                ln += part[:-12] + 'QCoreApplication'

                        else:
                            ln += part
                        ln = ln + '.translate'

                    ln = ln[:-11]
                    if '.UnicodeUTF8' in ln:
                        parts = ln.split('.UnicodeUTF8')
                        ln = ''
                        for part in parts:
                            if part.endswith('QApplication'):
                                if part.endswith('QtWidgets.QApplication'):
                                    part = part[:-22]

                                else:
                                    part = part[:-12]

                            # Maintain multilines syntax
                            part = part.rstrip(',').rstrip().rstrip(',')
                            ln = ln + part

                    lines[count] = ln + '\n'

                elif '.trUtf8(' in line:
                    lines[count] = line.replace('trUtf8', 'tr')

            count += 1

    def fix_wheelevent(self, lines):
        """Fix the wheelEvent event.delta() syntax.

        Args:
        lines -- the list of source code lines
        """
        # The function name must be matched by the re:
        # (?<=def wheelEvent\(self,)(.*?)(?=\):))
        count = 0
        while count < len(lines):
            line = lines[count]
            if self.is_code_line(line):
                if 'wheelEvent(' in line:
                    match = WHEEL_RE.search(line)
                    if match is not None:
                        indent = self.get_token_indent(line)
                        string = '%s.delta()' % match.group(0).strip()
                        count += 1

                        while count < len(lines):
                            line = lines[count]
                            if self.is_code_line(line):
                                if self.get_token_indent(line) <= indent:
                                    # End of wheelEvent function
                                    count -= 1
                                    break

                                if string in line:
                                    lines[count] = line.replace('.delta()',
                                                        '.angleDelta().y()')
                            count += 1
            count += 1

    def fix_layoutmargin(self, lines):
        """Replace the QLayout method setMargin() by setContentsMargins()

        Args:
        lines -- the list of source code lines
        """
        layouts = []
        m_re = re.compile('[, =\(\-+]')
        news = ('.setContentsMargins(', '.getContentsMargins()[0]')
        for line in lines:
            # Set the list of all layouts instanciated in the script
            if 'Layout(' in line:
                match = LAYOUT_RE.search(line.lstrip())
                if match is not None:
                    name = match.group(3)
                    if name and name.endswith(('QGrid', 'QVBox', 'QHBox')):
                        # If matched, group(1) is the reference of the layout
                        layouts.append(match.group(1).strip())

        for idx, line in enumerate(lines):
            if self.is_code_line(line):
                if '.setMargin(' in line:
                    parts = line.split('.setMargin(')
                    if parts[0].lstrip() in layouts:
                        val = parts[1].strip().rstrip(')').strip()
                        vals = ', '.join(val * 4)
                        lines[idx] = '%s%s%s)\n' %(parts[0], news[0], vals)

                elif '.margin(' in line:
                    ref = m_re.split(line.split('.margin')[0])[-1]
                    if ref in layouts:
                        lines[idx] = line.replace('.margin()', news[1])

    def fix_qdesktopservices(self, lines):
        """Replace QDesktopServices by QStandardPaths.

        This change is needed only for the methods displayName() and
        storageLocation()

        Args:
        lines -- the list of source code lines
        """
        fixme = "# FIXME$ Ambiguous syntax for QDesktopServices, "\
                                            "can't refactor it.\n"
        dsks = ('QDesktopServices()', 'QtGui.QDesktopServices()')
        for line in lines:
            if 'QDesktopServices' in line:
                match = DSK_RE.search(line.lstrip())
                if match is not None:
                    dsks.append(match.group(1).strip())

        count = 0
        while count < len(lines):
            line = lines[count]
            if not self.is_code_line(line):
                count += 1
                continue

            if '.displayName(' in line:
                method = '.displayName('

            elif '.storageLocation(' in line:
                method = '.storageLocation('

            else:
                count += 1
                continue

            parts = line.split(method)
            sub = parts[0].split('=')
            if sub[1].strip() in dsks:
                val = parts[1].strip().rstrip(')').strip()
                try:
                    loc = val.split('.')[1]
                except IndexError:
                    indent = self.get_token_indent(line)
                    lines.insert(count, '%s%s' % (indent, fixme))
                    count += 1

                else:
                    method = method.replace('storage', 'writable')
                    cls = 'QStandardPaths'
                    lines[count] = '%s = %s%s%s.%s)\n' %(sub[0].rstrip(), cls,
                                                            method, cls, loc)
                    self.modified['QStandardPaths'] = True

            count += 1

    def fix_qdate(self, lines):
        """Change QDate.setYMD() method to QDate.setDate().

        Args:
        lines -- the list of source code lines
        """
        gen = self.find_subclassed_class(lines, 'QDate')
        while 1:
            try:
                num = next(gen)
                self.fix_instance_qdate(lines, num + 1)
            except StopIteration:
                break

        dates = []
        for idx, line in enumerate(lines):
            if not self.is_code_line(line):
                continue

            if 'QDate(' in line:
                match = DATE_RE.search(line.lstrip())
                if match is not None:
                    dates.append(match.group(1).strip())

            if '.setYMD(' in line:
                inst = line.split('.setYMD')[0].lstrip()
                if inst in dates:
                    lines[idx] = line.replace('setYMD', 'setDate')

    def fix_instance_qdate(self, code, start):
        """Change QDate.setYMD() method to QDate.setDate() into a class wich
        inherits QDate

        Args:
        code -- the list of source code lines
        start -- the nummer of the second line of the class
        """
        for idx, line in enumerate(code[start:]):
            if self.is_class(line):
                break

            if 'self.setYMD(' in line:
                code[idx+start] = line.replace('setYMD', 'setDate')

    def fix_qgraphicsitem(self, lines):
        """Remove the scene from the arguments of a QGraphicsItem.

        The QGraphicsScene is identified with these rules:
            - Explicit: `scene=foo` or `scene` or `self.scene`
            - If args[-2] is None then args[-1] is scene
            - 1 arg: no scene possible
            - 2 args: if no keyword `parent` the scene is not identified,
                      a FIXME will be added
            - 3 args: args[2] is scene
            - 4 args: no scene possible
            - 6 args: args[5] is scene

        Args:
        lines -- the list of source code lines
        """
        # TODO replace scale(float x, float y) to setTransform(QMatrix) or
        # setScale(float) if float x == float y
        items = ['QAbstractGraphicsShapeItem',
                    'QGraphicsEllipseItem',
                    'QGraphicsItem',
                    'QGraphicsLineItem',
                    'QGraphicsPathItem',
                    'QGraphicsPixmapItem',
                    'QGraphicsPolygonItem',
                    'QGraphicsRectItem',
                    'QGraphicsSimpleTextItem',
                    'QGraphicsTextItem']
        self.fixed = []
        for item in items:
            self.find_graphics_items(lines, item)

    def find_graphics_items(self, code, obj):
        fixme = "# FIXME$ Can't identify the QGraphicsScene in the arguments "\
                                                        "of the QGraphicsItem"
        count = 0
        while count < len(code):
            scene = False
            line = code[count]
            if not self.is_code_line(line):
                count += 1
                continue

            if obj in line:
                if self.is_class(line):
                    count = self.refactor_qgraphics_subclass(code, count, obj)
                    continue

                parts = line.split(obj)
                if parts[1].startswith('Group'):
                    # Case of QGraphicsItemGroup
                    parts[0] += 'Group'
                    parts[1] = parts[1][5:]

                if not parts[1].startswith(('(', ' (')):
                    # Not instanciated
                    count += 1
                    continue

                try:
                    ref, _ = parts[0].split('=')
                except ValueError:
                    # Unknow object
                    count += 1
                    continue

                ind = self.get_token_indent(line)
                args = self.get_args(parts[1])
                scene, args = self.find_keyword(args)
                if not scene:
                    if len(args) < 2 or len(args) == 4:
                        count += 1
                        continue

                    if len(args) in [3, 6] or args[-2].strip() == 'None':
                        scene = args.pop().strip()

                    else:
                        code.insert(count, '%s%s\n' %(ind, fixme))
                        count += 2
                        continue

                code[count] = line.replace(parts[1], '(%s)\n' % ', '.join(args))

                if scene and scene != 'None':
                    string = '%s%s.addItem(%s)\n' %(ind, scene, ref.strip())
                    count += 1
                    code.insert(count, string)

            count += 1

    def refactor_qgraphics_subclass(self, lines, count, item):
        fixme = "# FIXME$ Can't identify the QGraphicsScene in arguments of "\
                                                        "the QGraphicsItem"
        cls = self.get_classname(lines[count])
        count += 1
        indent = ''
        while count < len(lines):
            scene = False
            line = lines[count]
            if not self.is_code_line(line):
                count += 1
                continue

            if line.lstrip().startswith('def __init__'):
                indent = self.get_token_indent(line)
                count += 1
                continue

            elif line.lstrip().startswith('super(%s' % cls):
                ind = self.get_token_indent(line)
                parts = line.split('__init__')
                args = self.get_args(parts[1])

            elif '%s.__init__' % item in line:
                ind = self.get_token_indent(line)
                parts = line.split('__init__')
                args = self.get_args(parts[1])

            elif self.get_token_indent(line) < indent:
                # Leaving the class
                return count + 1

            else:
                count += 1
                continue

            scene, args = self.find_keyword(args)
            if not scene:
                if len(args) < 2 or (len(args) == 2 and 'parent' in args[1]):
                    return count + 1

                if len(args) == 3 or args[-2].strip() == 'None':
                        scene = args.pop().strip()

                else:
                    lines.insert(count, '%s%s\n' %(ind, fixme))
                    return count + 1

            lines[count] = line.replace(parts[1], '(%s)\n' % ', '.join(args))
            if scene != 'None':
                count += 1
                lines.insert(count, '%sif %s is not None: %s.addItem(self)\n'
                                %(ind, scene, scene))

            return count + 1

        return count + 1

    def get_args(self, string):
        # Remove the parenthesis
        string = string.strip()[1:-1]
        return string.split(',')

    def find_keyword(self, args):
        scene = False
        for idx in range(len(args)):
            if args[idx].lstrip().startswith(('scene=', 'scene =')):
                scene = args.pop(idx).split('=')[1].strip()
                break

            elif args[idx].strip() in ['scene', 'self.scene']:
                scene = args.pop(idx).strip()
                break

        return scene, args

    def fix_qheader(self, lines):
        """Rename some QHeaderView's methods.

        Args:
        code -- the list of source code lines
        """
        headers = ['horizontalHeader()', 'verticalHeader()']
        for line in lines:
            if '.horizontalHeader()' in line or '.verticalHeader()' in line:
                try:
                    ref, _ = line.split('=')
                    headers.append(ref.strip())
                except:
                    pass

        headers = tuple(headers)
        olds = ('.setMovable', '.isMovable',
                '.setClickable', '.isClickable',
                '.setResizeMode', '.resizeMode')
        news = ('.setSectionsMovable', '.sectionsMovable',
                '.setSectionsClickable', '.sectionsClickable',
                '.setSectionResizeMode', '.sectionResizeMode')
        for old, new in zip(olds, news):
            gen = self.find_string(lines, old)
            while 1:
                try:
                    num = next(gen)
                    begin, _ = lines[num].split(old)
                    if begin.endswith(headers):
                        lines[num] = lines[num].replace(old, new)
                except StopIteration:
                    break

    def fix_qinputdialog(self, lines):
        """Replace the method getInteger() by getInt() in QInputDialog class.


        Args:
        code -- the list of source code lines
        """
        for idx, line in enumerate(lines):
            if 'QInputDialog.getInteger(' in line:
                lines[idx] = line.replace('.getInteger(', '.getInt(')

    def fix_qchar(self, lines):
        """Replace QChar() by unichr() for Python 2 and chr() for Python 3.

        Args:
        code -- the list of source code lines
        """
        is_qchar = False
        for idx, line in enumerate(lines):
            if self.is_code_line(line):
                if 'QChar' in line:
                    is_qchar = True
                    lines[idx] = line.replace('QtCore.QChar', 'QChar')

        if is_qchar:
            for idx in range_(len(lines)):
                if self.is_class(lines[idx]) or self.is_function(lines[idx]):
                    ind = self.find_next_indent(lines[idx+1:])
                    if not ind:
                        ind = "    "

                    text = "try:\n%sQChar = unichr\nexcept NameError:\n"\
                            "%s#Python 3\n%sQChar = chr\n\n" % (ind, ind, ind)
                    lines.insert(idx-1, text)
                    break

    def find_subclassed_class(self, code, classname):
        """Find a class instanciation wich subclass a Qt class.

        Args:
        code -- the list of source code lines
        classname -- the name of the subclassed class

        Returns:
        int(nummer of class line)
        """
        for idx, line in enumerate(code):
            if self.is_class(line):
                try:
                    if classname in line.split('(')[1]:
                        yield idx
                except:
                    pass

    def find_string(self, code, string):
        """Find a string into a source code.

        Args:
        code -- the list of source code lines
        string -- the string

        Returns:
        int(nummer of line code) if found
        """
        for idx, line in enumerate(code):
            if string in line:
                yield idx

    def clean_args(self, string):
        """Returns the list of arguments of an emit() method.

        Args:
        string -- The last part of the line code
        """
        elem = string.split(',')
        if len(elem) > 1:
            return [e.strip().strip(')').strip() for e in elem[1:]]

        return []

    def count_ref(self, string):
        return len(string.split(','))

    def replace_classnames(self, lines):
        """Rename some classe's names.

        QMatrix to QTransform
        QIconEngineV2 to QIconEngine

        Args:
        lines -- source code
        """
        olds = ['QMatrix', 'QIconEngineV2']
        news = ['QTransform', 'QIconEngine']
        for idx, line in enumerate(lines):
            if self.is_code_line(line):
                for old, new in zip(olds, news):
                    line = line.replace(old, new)
                    lines[idx] = line

    def is_code_line(self, line):
        """Returns True if a line is not empty, nor a comment, nor a docstring.

        Args:
        line -- the line code

        Returns:
        True if line is a valid code line
        """
        if not line.strip() or self.is_comment(line) or self.is_docstring(line):
            return False

        return True

    def is_comment(self, line):
        """Returns True if a line is a comment.

        Args:
        line -- the line code
        """
        try:
            return line.lstrip()[0] == '#'
        except IndexError:
            # Empty line
            return False

    def is_docstring(self, line):
        """Returns True if a line is a docstring.

        Args:
        line -- the line code
        """
        return line.lstrip().startswith(('"""', "'''"))

    def is_class(self, line):
        """Returns True if a line is a class definition line.

        Args:
        line -- the line code
        """
        return line.lstrip().startswith('class ')

    def is_function(self, line):
        """Returns True if a line is a function definition line.

        Args:
        line -- the line code
        """
        return line.lstrip().startswith('def ')

    def get_classname(self, string):
        """Returns the name of a class.

        Args:
        string -- the class's definition line code
        """
        match = CLS_RE.search(string)
        if match is not None:
            return match.group(0).strip()

    def get_token_indent(self, string):
        """Returns the indentation of a line.

        args:
        string -- the line
        """
        ind = tokenize.INDENT
        tokens = tokenize.generate_tokens(StringIO(string).readline)
        for typ, chain, _, _, _ in tokens:
            if typ == ind:
                return chain

            return ''

    def find_next_indent(self, lines):
        """Returns the first indentation found into a list of lines.

        Args:
        lines -- the list of lines
        """
        for line in lines:
            indent = self.get_token_indent(line)
            if indent:
                return indent

            return ''

    def count_parenthesis(self, line, start, end):
        """Count the occurrences of an open parenthesis into a string.

        Args:
        line -- the string
        start -- the word where the count begin
        end -- the word where the count finish

        Returns:
        int(occurences)
        """
        tokens = tokenize.generate_tokens(StringIO(line).readline)
        for _, st, _, _, _ in tokens:
            if st == start:
                count = 0

            elif st == '(':
                count += 1

            elif st == end:
                return count

    def find_closing_parenthesis(self, line, start=False):
        """Find the closing parenthesis according to a given opening parenthesis.

        Args:
        line -- one logical line of code
        start -- the word that precede the opening parenthesis

        Returns:
        tuple(begin, end) where begin is the column of the opening parenthesis
                          and end is the column of the closing parenthesis
        """
        begin = not start
        count = 0
        tokens = tokenize.generate_tokens(StringIO(line).readline)
        for _, st, bg, _, _ in tokens:
            if start and st == start:
                begin = True
                if start == '(':
                    count += 1
                    first = bg[1]

            elif begin and st == '(':
                if not count:
                    first = bg[1]
                count += 1

            elif count and st == ')':
                count -= 1
                if not count:
                    return first, bg[1]

        return 0, len(line)

    def refactor_signal_instances(self, string):
        """Refactor the multiple pyqtSignal instance.

        Args:
        string -- the line

        Returns:
        list(strings)
        """
        sigs = string.split('=')[1].strip()
        match = [s for s in SIG_RE['pysig_re'].findall(sigs) if len(s) > 2]
        lines = []
        for m in match:
            sig, arg = m.split('(')
            lines.append("%s = pyqtSignal(%s" %(sig, arg.replace("QString",
                                                                "'QString'")))

        return lines

    def remove_fromutf8(self, strings):
        for idx, string in enumerate(strings):
            if not '_fromUtf8(' in string:
                continue

            caput, cauda = string.split('_fromUtf8(')
            # find the pos of the closing parenthesis
            pos = cauda[1:].index(cauda[0]) + 2
            if len(cauda) < pos + 1:
                strings[idx] = caput + cauda[0:pos]

            else:
                strings[idx] = caput + cauda[0:pos] + cauda[pos+1:]

        return strings

    def get_signal(self, strings):
        sig = strings.pop(0)
        for idx, s in enumerate(strings):
            if sig.endswith(('")', "')")):
                return sig, strings[idx:]

            sig = sig + ', ' + s

    def refactor_signal(self, string):
        olds = ('clicked(bool)', 'clicked()', 'triggered(bool)', 'triggered()')
        news = ('clicked[bool]', 'clicked[()]', 'triggered[bool]', 'triggered[()]')
        match = SIG_RE['sig_re'].search(string)
        if match is not None:
            sig = match.group(0)
            try:
                idx = olds.index(sig)
                return news[idx]
            except ValueError:
                pass

            return self.clean_signal(sig)

        return False

    def get_slot(self, seq):
        slot = ''
        for string in seq:
            if 'SLOT' in string:
                match = SIG_RE['slot_re'].search(string)
                if match is not None:
                    slot = match.group(0).split('(')[0]
                    break
        if slot:
            seq.remove(string)

        return slot.strip()

    def clean_signal(self, signal):
        signal = signal.replace('()', '').replace(' *', '').replace('*', '')
        signal = signal.replace('(', '[').replace(')', ']')
        signal = signal.replace("const ", '').replace('&', '')
        return signal.replace('QString', "'QString'")

    def change_import_lines(self, lines):
        """Refactor the import's lines.

        Args:
        lines -- list of lines of source code

        Returns:
        list(lines)
        """
        news = []
        count = 0
        def set_qstandardpaths(txt):
            if self.modified['QStandardPaths']:
                news.append(txt.replace('PyQt4', 'PyQt5') +
                            '.QtCore import QStandardPaths\n')
                self.modified['QStandardPaths'] = False

        while count < len(lines):
            line = lines[count]

            if 'import ' in line:
                line = line.replace(', SIGNAL', '')
                line = line.replace('SIGNAL', '')

            if 'from PyQt4.QtCore ' in line and self.modified['QStandardPaths']:
                news.append(line.replace('PyQt4', 'PyQt5').rstrip() +
                            ', QStandardPaths\n')
                self.modified['QStandardPaths'] = False

            elif 'from PyQt4.QtCore ' in line and 'QChar' in line:
                elems = [c.strip() for c in line[25:].split(',')]
                elems.remove('QChar')
                if not elems:
                    count += 1
                    continue

                news.append('from PyQt5.QtCore import ' + ', '.join(elems) + '\n')

            elif 'from PyQt4 import' in line:
                line = self.refactor_modules_import(line)
                txt = self.reindent_import_line(line)
                news.append(txt)
                set_qstandardpaths(line.split(' import')[0])

            elif 'from PyQt4.Qt import ' in line:
                parts = line.split('import ')
                gui, wdg, pr, md, ogl = self.sort_qt_classes(parts[1])

                if gui:
                    stgui = "".join([parts[0].replace('PyQt4', 'PyQt5'),
                                    'import ', ', '.join(gui)])
                    txt = self.reindent_import_line(stgui)
                    news.append(txt)

                if wdg:
                    stwdg = "".join([parts[0].replace('PyQt4.Qt',
                                    'PyQt5.QtWidgets import '), ', '.join(wdg)])
                    txt = self.reindent_import_line(stwdg)
                    self._has_qtwidget_import = True
                    news.append(txt)

                if pr:
                    stpr = "".join([parts[0].replace('PyQt4.Qt',
                                'PyQt5.QtPrintSupport import '), ', '.join(pr)])
                    txt = self.reindent_import_line(stpr)
                    news.append(txt)

                if md:
                    stmd = "".join([parts[0].replace('PyQt4.Qt',
                                'PyQt5.QtMultimedia import '), ', '.join(md)])
                    txt = self.reindent_import_line(stmd)
                    news.append(txt)

                if ogl:
                    stogl = "".join([parts[0].replace('PyQt4.Qt',
                                'PyQt5.QtOpenGL import '), ', '.join(ogl)])
                    txt = self.reindent_import_line(stogl)
                    news.append(txt)
                set_qstandardpaths(line.split('.Qt')[0])

            elif 'from PyQt4.QtGui ' in line:
                parts = line.split('import')
                gui, wdg, pr, md = self.sort_qtgui_classes(parts[1])
                if gui:
                    stgui = "".join([parts[0].replace('PyQt4', 'PyQt5'),
                                    'import ', ', '.join(gui)])
                    txt = self.reindent_import_line(stgui)
                    news.append(txt)

                if wdg:
                    stwdg = "".join([parts[0].replace('PyQt4.QtGui',
                                    'PyQt5.QtWidgets import '), ', '.join(wdg)])
                    txt = self.reindent_import_line(stwdg)
                    self._has_qtwidget_import = True
                    news.append(txt)

                if pr:
                    stpr = "".join([parts[0].replace('PyQt4.QtGui',
                                'PyQt5.QtPrintSupport import '), ', '.join(pr)])
                    txt = self.reindent_import_line(stpr)
                    news.append(txt)

                if md:
                    stmd = "".join([parts[0].replace('PyQt4.QtGui',
                                'PyQt5.QtMultimedia import '), ', '.join(md)])
                    txt = self.reindent_import_line(stmd)
                    news.append(txt)
                set_qstandardpaths(line.split('.QtGui')[0])

            elif 'from PyQt4.QtWebKit ' in line:
                parts = line.split('import')
                wb, wdg = self.sort_qtwebkit_classes(parts[1])
                if wb:
                    chain = "".join([parts[0].replace('PyQt4', 'PyQt5'),
                                    'import ', ', '.join(wb)])
                    txt = self.reindent_import_line(chain)
                    news.append(txt)

                if wdg:
                    chain = "".join([parts[0].replace('PyQt4.QtWebKit',
                                    'PyQt5.QtWebKitWidgets'),
                                    'import ', ', '.join(wdg)])
                    txt = self.reindent_import_line(chain)
                    news.append(txt)

            else:
                line = line.replace('PyQt4', 'PyQt5')
                news.append(line)

            count += 1

        return news

    def refactor_modules_import(self, line):
        """Apply the changes to a import line.

        Args:
        line -- the line
        """
        parts = line.split('import')
        chain = parts[0].replace('PyQt4', 'PyQt5') + 'import '
        end = parts[1].replace('(', '').replace(')', '').replace('\\', '')
        modules = [name.strip() for name in end.split(',')]
        if 'QtGui' in modules and not self.modified['QtGui']:
            modules.remove('QtGui')

        if self.modified['QtCore']:
            modules.append('QtCore')

        if self.modified['QtWidgets']:
            modules.append('QtWidgets')
            self._has_qtwidget_import = True

        if 'QtWebKit' in modules and not self.modified['QtWebKit']:
            modules.remove('QtWebKit')

        if self.modified['QtWebKitWidgets']:
            modules.append('QtWebKitWidgets')

        if self.modified['QtMultimedia'] and not 'QtMultimedia' in modules:
            modules.append('QtMultimedia')

        if self.modified['QtPrintSupport']:
            modules.append('QtPrintSupport')

        return chain + ', '.join(modules) + '\n'

    def sort_qtgui_classes(self, chain):
        """Sort the classes from a QtGui import line.

        Args:
        chain -- the classe's names in one line

        Returns:
        Four lists: QtGui, QtWidgets, QtPrintSupport and QtMultimedia classes
        """
        names = chain.split(',')
        olds = []
        widgets = []
        printer = []
        medias = []
        for name in names:
            name = name.replace('\\', '')
            cls = name.strip().replace('(', '').replace(')', '')
            if not cls:
                continue

            if cls in CLASSES['QtWidgets']:
                widgets.append(cls)

            elif cls in CLASSES['QtMultimedia']:
                medias.append(cls)

            elif cls in CLASSES['QtPrintSupport']:
                printer.append(cls)

            else:
                if cls == 'QIconEngineV2':
                    cls = 'QIconEngine'
                elif cls == 'QMatrix':
                    cls = 'QTransform'
                olds.append(cls)

        return olds, widgets, printer, medias

    def sort_qt_classes(self, chain):
        """
        Sort the classes from a qt import line

        Args:
        chain -- the classe's names in one line

        Returns:
        Five lists: Qt, QtWidgets, QtPrintSupport, QtMultimedia and QtOpenGl classes
        """
        olds, widgets, printer, medias = self.sort_qtgui_classes(chain)
        opengl = []
        gui = []
        for cls in olds:
            if cls in CLASSES['QtOpenGL']:
                opengl.append(cls)
            else:
                gui.append(cls)
        return gui, widgets, printer, medias, opengl

    def sort_qtwebkit_classes(self, chain):
        """Sort the classes from a QtWebkit import line.

        Args:
        chain -- the classe's names in one line

        Returns:
        Two lists: QtWebkit and QtWebKitWidgets classes
        """
        names = chain.split(',')
        olds = []
        news = []
        for name in names:
            name = name.replace('\\', '')
            cls = name.strip().replace('(', '').replace(')', '')
            if not cls:
                continue

            if cls in CLASSES['QtWebKitWidgets']:
                news.append(cls)

            else:
                olds.append(cls)

        return olds, news

    def reindent_import_line(self, line):
        """Rewrite a long import line into a multiline.

        The lines have maximum 80 caracters and the indentations are fixed at
        the column of the first open parenthesis of the first line.

        Args:
        line -- the original line

        Returns:
        Multiline
        """
        if len(line) < 81:
            return line + '\n'

        begin, end = line.split('import')
        txt = begin + 'import ('
        cls = end.lstrip().split(',')
        lines = []
        indent = self.get_import_indent(len(txt)-1)
        for cl in cls:
            cl = cl.rstrip() + ','
            if len(txt) + len(cl) < 81:
                txt += cl

            else:
                txt += '\n'
                lines.append(txt)
                txt = indent + cl

        lines.append(txt[:-1] + ')')

        return "".join(lines) + '\n'

    def get_import_indent(self, length):
        """Returns the indentation for a multiline import.

        Args:
        length -- the length of the string `from foo import`

        returns:
        str()
        """
        if self.indent == ' ':
            return ' ' * length

        # Assume a tab is equivalent of four spaces
        return self.indent * (length / 4)

    def clean_file(self, lines):
        fixs = []
        lineno = 1
        for i, line in enumerate(lines):
            if self.is_comment(line):
                if 'FIXME$' in line:
                    lines[i] = line.replace('FIXME$', 'FIXME')
                    fixs.append('%6d %s' %(lineno, line.lstrip().lstrip('# FIXME$')))
            lineno += line.count('\n')

        return lines, fixs

    def rcut(self, string, chars):
        """Remove the trailing characters from a string.

        Args:
        string -- the string
        chars -- the sequence of characters
        """
        if string.endswith(chars):
            string = string[:-len(chars)]

        return string

    def convert_in_one_line(self, strings):
        lines = strings.split('\n')
        if len(lines) > 1:
            return lines[0] + ''.join(l.lstrip() for l in lines[1:])

        return strings

    def save_changes(self, lines):
        if PY_VERS < 3:
            with open(self.dest, 'wb') as outf:
                for line in lines:
                    outf.write((line.replace('\n', L_SEP)).encode(self.tools.encoding))

        else:
            with open(self.dest, 'w') as outf:
                #remove = False
                for line in lines:
                    ## Somwhere in this script a blank line
                    ## is added every blank line: here we remove a blank line
                    ## every blank line
                    #if line == '\n':
                        #remove = not remove
                        #if remove:
                            #continue
                    l = line.replace('\n', str(L_SEP))
                    outf.write(line)

    def print_(self, msg):
        sys.stdout.write('%s\n' % msg)
        with open(self.log, 'a') as outf:
            if PY_VERS < 3:
                outf.write(('%s%s' % (msg, L_SEP)).encode(self.tools.encoding))

            else:
                outf.write('%s%s' % (msg, L_SEP))


class Tools(object):
    def __init__(self):
        self.encoding = 'utf-8'
        self.last_error = ''

    def read_python_source(self, filename):
        """Return the source code.

        Args:
        filename -- the file name

        Returns:
        list(lines)
        """
        self.encoding = self.get_encoding(filename)
        if self.encoding is None:
            return

        return self.get_content(filename)

    def get_content(self, filename):
        if PY_VERS < 3:
            try:
                with open_(filename, "r", encoding=self.encoding) as inf:
                    content = inf.read().replace("\r\n", "\n")
            except IOError as why:
                self.last_error = why
                return False

        else:
            try:
                with open(filename, "r", encoding=self.encoding) as inf:
                    content = inf.read()
            except IOError as why:
                self.last_error = why
                return False

        return content.split('\n')

    def get_encoding(self, path):
        lines = []
        try:
            with open(path, 'rb') as inf:
                try:
                    lines.append(inf.readline())
                    lines.append(inf.readline())
                except:
                    pass
        except IOError as why:
            sys.stdout.write('Cant read the file %s\n' % path)
            return

        return self.read_encoding(lines)

    def read_encoding(self, lines):
        l1, l2 = lines
        coding = None
        bom = False
        default = 'utf-8'
        if not lines or lines == [b'', b'']:
            return default

        if l1.startswith(BOM_UTF8):
            bom = True
            l1 = l1[3:]
            default = 'utf-8-sig'
            if not l1:
                return default

        coding = self.find_comment(l1, bom)
        if coding is not None:
            return coding

        coding = self.find_comment(l2, bom)
        if coding is not None:
            return coding

        return default

    def find_comment(self, chain, bom):
        comment = re.compile("coding[:=]\s*([-\w.]+)")
        try:
            string = chain.decode('ascii')
        except UnicodeDecodeError:
            return None

        matches = comment.findall(string)
        if not matches:
            return None

        codings = ("latin-1-", "iso-8859-1-", "iso-latin-1-")
        enc = matches[0][:12].lower().replace("_", "-")
        if enc == "utf-8" or enc.startswith("utf-8-"):
             encoding ="utf-8"

        elif enc in codings or enc.startswith(codings):
            encoding = "iso-8859-1"

        try:
            codec = lookup(encoding)
        except LookupError:
            sys.stdout.write("Can't read the encoding: %s\n" % encoding)
            return

        if bom:
            if codec.name != 'utf-8':
                sys.stdout.write("Inconsistant encoding: %s\n" % encoding)
                return
            encoding += '-sig'
        return encoding

    def get_code_lines(self, filename):
        count = 0
        source = self.read_python_source(filename)

        orig = ['%s\n' % l for l in source]
        if len(orig) == 1:
            self.last_error = 'File is empty'
            return False

        lines = []
        gen = self.get_num_physical_lines(filename)
        while 1:
            try:
                num = next(gen)
                if not num:
                    return False
                lines.append(''.join(orig[count:num]))
                count = num
            except StopIteration:
                break

        return lines

    def get_num_physical_lines(self, filename):
        """Returns the line nummer where a logical line ending.

        The converter works with a list of logical lines, not physical lines.

        Args:
        filename -- the file name

        Returns:
        int(lineno)
        """
        if PY_VERS < 3:
            inf = open_(filename, "r", encoding=self.encoding)
            src = inf.readline

        else:
            inf = open(filename, "r", encoding=self.encoding)
            src = inf.readline

        newline = tokenize.NEWLINE
        comment = tokenize.COMMENT
        nl = tokenize.NL
        indent = tokenize.INDENT
        strng = tokenize.STRING
        ind = False
        new = False
        com = False
        tokens = tokenize.generate_tokens(src)
        try:
            for typ, ch, bg, end, ln in tokens:
                if typ == newline:
                    # End of logical line
                    new = True
                    yield end[0]

                elif typ == comment and new:
                    # One line comment
                    ind = False
                    com = True
                    new = True
                    yield end[0]

                elif typ == nl and not len(ln.strip()):
                    # Empty line
                    ind = False
                    new = True
                    yield end[0]

                elif typ == nl:
                    # End of physical line
                    if com:
                        com = False
                        new = True
                    else:
                        new = False

                elif typ == indent:
                    # Needed for the next comparison
                    ind = True

                elif typ == strng and ind:
                    # The only way to get the end of a docstring
                    ind = False
                    yield end[0]

                else:
                    ind = False
        except Exception as why:
            sys.stdout.write('Except: %s\nLine: %s\n%s' %(why, end, ln))
            self.last_error = why
            yield False

        finally:
            inf.close()


class Main(object):
    def __init__(self, args):
        self.path = None
        self.nosubdir = False
        self.destdir = None
        self.write_diff = False
        self.write_diffs = False
        self.filename_diff = False
        self.log = 'pyqt4_to_pyqt5.log'
        parser = argparse.ArgumentParser(description='Convert a source code '
                        'written for PyQt4 into a valid code for PyQt5')
        parser.add_argument("path",
                        help="Path of a file or a directory.\nThe file may be "
                        "a source code python or a text file wich contains the "
                        "names of the files to be converted separated by a new "
                        "line.")
        parser.add_argument("--nosubdir", action="store_true",
                        help="Don't process into sub-directories."
                        "  Default: False")
        parser.add_argument("-o", nargs=1, help="The name of the generated "
                        "file or directory if path is a directory. "
                        "Default: path_PyQt5")
        parser.add_argument("--diff", nargs='?', const='same_as',
                        help="Write a diff file. If there's more than one file "
                        "converted, all the diff are written into one file. "
                        "If no name is provided, the diff file will be named "
                        "with the name of the source."
                        "  Default: False")
        parser.add_argument("--diffs", action="store_true",
                        help="Write a diff file for each file converted."
                        "The diff files will be created in the same destination "
                        "dir as the converted files"
                        "  Default: False")
        arg = parser.parse_args()

        if arg.path:
            self.path = self.check_path(arg.path)
            if not self.path:
                sys.exit()

        if arg.nosubdir:
            self.nosubdir = True

        if arg.diff:
            self.write_diff = arg.diff

        if arg.diffs:
            self.write_diffs = True

        if arg.o:
            self.destdir = self.check_path(arg.o[0], True)
            if not self.destdir:
                sys.exit()

        else:
            self.destdir = self.path

        date = datetime.now().strftime("%A %d. %B %Y %H:%M")
        self.print_('**  pyqt4_to_pyqt5.log  %s  **\nArgs: %s\n' %(date, sys.argv))
        self.prepare_changes()

    def prepare_changes(self):
        if os.path.isdir(self.path):
            if self.destdir == self.path:
                self.destdir = self.path + "_PyQt5"

            self.copy_dir(self.destdir, self.path)
            self.set_diff_option('dir')
            self.process_from_dir(self.destdir)

        elif os.path.isfile(self.path):
            if os.path.splitext(self.path)[1] not in PYEXT:
                # Assume this is a list of files
                files, subdirs = self.read_filenames(self.path)
                if self.destdir == self.path:
                    self.destdir = "__PyQt5__"

                self.copy_files(self.destdir, subdirs, files)
                self.set_diff_option('dir')
                self.process_from_dir(self.destdir)

            else:
                if self.destdir == self.path:
                    f, e = os.path.splitext(self.path)
                    self.destdir = "".join([f, "_PyQt5", e])

                if self.write_diff:
                    self.set_diff_option('file')
                cnv = PyQt4ToPyQt5(self.path, self.destdir, self.log)
                cnv.setup()
                self.write_diff_file(self.destdir, self.path)

    def process_from_dir(self, fld):
        self.print_('Beginning into: %s\n' % fld)
        for root, dirs, files in os.walk(fld):
            files.sort()
            for f in files:
                fname = os.path.join(root, f)
                cnv = PyQt4ToPyQt5(fname, fname, self.log)
                cnv.setup()
                self.write_diff_file(fname)

    def copy_dir(self, dest, orig):
        self.copied = {}
        try:
            os.makedirs(dest)
        except Exception as why:
            sys.stdout.write("Can't create the destination\nReason: %s\n" % why)
            sys.exit()

        if self.nosubdir:
            files = glob(os.path.join(orig, '*.py'))
            for f in files:
                shutil.copy(f, dest)
                self.copied[dest] = f
            return

        for root, dirs, files in os.walk(orig):
            target = root.replace(orig, dest)
            for name in dirs:
                if name != '__pycache__':
                    os.makedirs(os.path.join(target, name))

            for name in files:
                if os.path.splitext(name)[1] in PYEXT:
                    src = os.path.join(root, name)
                    cp = os.path.join(target, name)
                    shutil.copy(src, cp)
                    self.copied[cp] = src

    def read_filenames(self, path):
        try:
            with open(path, 'r') as inf:
                files = [f.strip() for f in inf.readlines()]
        except IOError as why:
            sys.stdout.write("Can't read the file: `%s`\nReason: %s\n"
                                %(path, why))
            sys.exit()

        files.sort()
        dirs = set([])
        for f in files:
            d = os.path.dirname(f)
            if d:
                dirs.add(d)
        dirs = list(dirs)
        dirs.sort()

        return files, dirs

    def copy_files(self, dest, dirs, files):
        self.copied = {}
        if not os.path.exists(dest):
            try:
                os.makedirs(dest)
            except Exception as why:
                sys.stdout.write("Can't create the dir: `%s`\nReason: %s\n"
                                    %(dest, why))
                sys.exit()

        for f in files:
            if not os.path.isfile(f):
                sys.stdout.write('File `%s` not found, ignored\n' % f)
                continue
            cp = os.path.join(dest, os.path.basename(f))
            shutil.copy(f, cp)
            self.copied[cp] = f

    def check_path(self, path, writable=False):
        if not os.path.isabs(path):
            path = os.path.normpath(os.path.join(os.getcwd(), path))

        if not writable:
            if not os.path.exists(path):
                sys.stdout.write('No such file: `%s`\n' % path)
                return False

            return path

        parent = os.path.dirname(path)
        if not os.access(parent, os.W_OK):
            sys.stdout.write('Destination dir `%s` is read only\n' % parent)
            return False

        return path

    def set_diff_option(self, opt):
        """Sets the diff file option.

        """
        if not any((self.write_diff, self.write_diffs)):
            return

        if opt == 'file':
            # Convert just one file
            if self.write_diff == 'same_as':
                # Using same name as the file converted
                self.filename_diff = 'destfile'

            elif os.path.isdir(self.write_diff):
                if not self.check_path(self.write_diff, True):
                    sys.stdout.write('Dir `%s` is read only\n' % self.write_diff)
                    self.write_diff = False
                    return

                # Using provided dir path + the name of the file converted
                fname = os.path.splitext(os.path.basename(self.destdir))[0] + '.diff'
                self.filename_diff = os.path.join(self.write_diff, fname)

            else:
                # Using diff file name provided
                self.filename_diff = self.write_diff

        elif opt == 'dir':
            # Convert several files in dir(s)
            if self.write_diffs:
                # One diff file for each file converted
                self.filename_diff = 'destfile'

            elif self.write_diff == 'same_as':
                # Using one file diff into the destination dir
                self.filename_diff = os.path.join(self.destdir, 'DIFFs.diff')

            else:
                if os.path.isdir(self.write_diff):
                    if not self.check_path(self.write_diff, True):
                        sys.stdout.write('Dir `%s` is read only\n' % self.write_diff)
                        self.write_diff = False
                        return

                    # Using provided dir path + DIFFs.diff
                    self.filename_diff = os.path.join(self.write_diff, 'DIFFs.diff')

                else:
                    # Using provided file path
                    self.filename_diff = self.write_diff

    def write_diff_file(self, dest, orig=None):
        if not self.filename_diff:
            return

        if self.filename_diff == 'destfile':
            diffname = os.path.splitext(dest)[0] + '.diff'
            self.print_('Write diff file: `%s`' % self.filename_diff)

        else:
            diffname = self.filename_diff

        if orig is None:
            orig = self.copied[dest]

        date = datetime.now().strftime("%A %d. %B %Y %H:%M")
        cmd = " ".join(['diff', orig, dest])
        with open(diffname, 'a') as outf:
            outf.write('\n** Diff file created by pyqt4topyqt5.py %s **\n' % date)
            outf.write('<\t%s\n>\t%s\n' % (orig, dest))
            reply = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE)
            outf.write(str(reply.communicate()[0]))

    def print_(self, msg):
        with open(self.log, 'a') as outf:
            outf.write('%s\n' % msg)

if __name__ == '__main__':
    main = Main(sys.argv)


