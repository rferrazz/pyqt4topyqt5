#!/usr/bin/env python
# -*- coding: utf-8 -*-

# pyqt4topyqt5.py
# Nov. 12 2013
# Author: Vincent Vande Vyvre <vincent.vandevyvre@swing.be>
# Copyright: 2013 Vincent Vande Vyvre
# Licence: LGPL3

import os
import glob
import re
import shutil
import argparse
import sys
import tokenize
import subprocess
import stat

from datetime import datetime
from codecs import BOM_UTF8, lookup, open as open_

PY_VERS = sys.version_info[0]

if PY_VERS < 3:
    from StringIO import StringIO
    range_ = xrange
else:
    from io import StringIO
    range_ = range

from qtclass import MODULES, CLASSES, DISCARDED, QAPP_STATIC_METHODS, QVARIANT_OBSOLETE_METHODS

L_SEP = os.linesep
PYEXT = (os.extsep + "py", os.extsep + "pxi")
PYSHEBANG = ("#!/usr/bin/env python", "#!/usr/bin/python")
MOD_RE = {'QtGui': re.compile(r'(?<=QtGui\.)(.*?)(?=[.\(\),\]:]|\Z)', re.DOTALL),
          'QtWebKit': re.compile(r'(?<=QtWebKit\.)(.*?)(?=[.\(\),\]:]|\Z)', re.DOTALL)}
SIG_RE = {'fun_re': re.compile(r'(?<=\()(.*)(?=\))', re.DOTALL),
          'sig_re': re.compile(r'''(?<=SIGNAL\(["' ])(.*?)(?=["',])''', re.DOTALL),
          'slot_re': re.compile(r'''(?<=SLOT\(["'])(.*?)(?=["'])''', re.DOTALL),
          'pysig_re': re.compile(r'''(?<=["'])(.*?)(?=["'])''', re.DOTALL)}
DOT_RE = re.compile(r'(?<=\()(.*?)(?=\.NoDotAndDotDot)')
WHEEL_RE = re.compile(r'(?<=def wheelEvent\(self,)(.*?)(?=\):)')
LAYOUT_RE = re.compile(r'(.*?)(\=)(.*?)(?=Layout\()')
DSK_RE = re.compile(r'(.*?)(\=)(.*?)(?=QDesktopServices\()')
DATE_RE = re.compile(r'(.*?)(\=)(.*?)(?=QDate\()')
CLS_RE = re.compile(r'(?<=class )(.*?)(?=[\(:])')

# Utils

def diff_parenthesis(line):
    opened = line.count('(')
    closed = line.count(')')
    return opened - closed


class PyQt4ToPyQt5(object):
    def __init__(self, source, dest, log, nopyqt5):
        self.log = log
        self.source = source
        self.dest = dest
        self.indent = ' '
        self.tools = Tools()

        self._has_qtwidget_import = False
        self._added_pyqtSignal = False
        self._pyqt5 = not nopyqt5

    def setup(self):
        self.print_('Processing file: `%s`' % self.source)
        self.modified = {'QtGui': False, 'QtWidgets': False,
                         'QtWebKit': False, 'QtWebKitWidgets': False,
                         'QtMultimedia': False, 'QSound': False,
                         'QtCore': False, 'QtPrintSupport': False,
                         'QStandardPaths': False}
        src = self.tools.get_code_lines(self.source)
        if src is None:
            self.print_('  Error: Unable to read the file: %s\n  Reason: %s\n'
                        % (self.source, self.tools.last_error))
            return

        try:
            self.indent = self.get_token_indent(''.join(src))[0]
        except IndexError:
            # Never seen a PyQt4 script without indentation, but ...
            self.indent = ' '

        # src is the list of logical lines code, NOT physical lines
        qt4, sig, gui, web = self.get_import_lines(src)
        if not any([qt4, sig, gui, web]):
            self.print_('  No changes needed.\n')
            return

        # call before updating signals and slots
        if self._pyqt5:
            self.remove_fromUtf8(src)

        # call before change_module_name
        if sig:
            self.fix_emit(src)
            self.fix_connect(src)
            self.fix_disconnect(src)
            self.fix_signal(src)
            self.fix_slot(src)

        if gui and self._pyqt5:
            src = self.change_module_name(src, 'QtGui', 'QtCore')
            src = self.change_module_name(src, 'QtGui', 'QtWidgets')
            src = self.change_module_name(src, 'QtGui', 'QtPrintSupport')

        if web and self._pyqt5:
            src = self.change_module_name(src, 'QtWebKit', 'QtWebKitWidgets')

        # call after the signals and slots have been fixed
        src = self.change_import_lines(src)

        if self._pyqt5:
            self.fix_qfiledialog(src)
            self.fix_qdir(src)
            self.fix_qwidget(src)
            self.fix_qtscript(src)
            self.fix_qtxml(src)
            self.fix_qtdeclarative(src)
            self.fix_qgraphicsitemanimation(src)
            self.fix_qtopengl(src)
            self.fix_translations(src)
            self.fix_wheelevent(src)
            self.fix_layoutmargin(src)
            self.fix_qdesktopservices(src)
            self.fix_qdate(src)
            self.fix_qgraphicsitem(src)
            self.fix_qheader(src)
            self.fix_qinputdialog(src)
            self.fix_qchar(src)
            self.fix_qstring(src)
            self.fix_qglobal(src)
            self.fix_qvariant(src)
            self.replace_classnames(src)
            self.replace_qApp(src)

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
        qt4 = sig = gui = web = False
        for line in lines:
            if self.is_code_line(line) and ('SIGNAL(' in line or 'SLOT(' in line or 'emit(' in line):
                sig = True
            if line.lstrip().startswith(('import ', 'from ')) and 'PyQt4' in line:
                qt4 = True
                if '.Qt' in line:
                    gui = True
                    web = True
                if 'QtGui' in line:
                    gui = True
                if 'QtWebKit' in line:
                    web = True
                if all([sig, gui, web]):
                    break

        return qt4, sig, gui, web

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
                names = MOD_RE[old_mod].findall(line)
                if not names:
                    news.append(line)
                    count += 1
                    continue

                new = []
                parts = line.split(old_mod)
                if line.startswith(old_mod):
                    name = names.pop(0).strip()
                    new.append(get_module_name(name))

                for part in parts[:-1]:
                    if not part:
                        continue

                    new.append(part)
                    try:
                        name = names.pop(0).strip()
                        new.append(get_module_name(name))
                    except IndexError:
                        indent = self.get_token_indent(line)
                        news.append("%s%s" % (indent, fixme))
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
        olds = ('.getOpenFileNamesAndFilter', '.getOpenFileNameAndFilter', '.getSaveFileNameAndFilter')
        news = ('.getOpenFileNames', '.getOpenFileName', '.getSaveFileName')
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
                        lines[count] = ''.join([line[:end+1], '[0]', line[end+1:], '\n'])

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
                        lines[idx] = line.replace('.NoDotAndDotDot', rep)

                if '.convertSeparators(' in line:
                    lines[idx] = line.replace('convertSeparators', 'toNativeSeparators')

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

                if self.is_code_line(l) and l.lstrip().startswith(('import ', 'from ')) and not '__future__' in l:
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
                    # Skip over strings (which may contain parentheses).
                    # TODO: handle triple-quoted strings
                    if function[i] == '"' or function[i] == "'":
                        str_delimiter = function[i]
                        while True:
                            slices[current] += function[i]
                            i += 1
                            if function[i] == str_delimiter:
                                break
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
        """Removes old-style signal/slot declarations which use the SIGNAL/SLOT nomenclature.

        Args:
        el -- string containing a signal/slot declaration

        Returns:
        list -- signal/slot name followed by signal/slot arguments
        """
        if "SIGNAL(" in el or "SLOT(" in el:
            # Note: This assumes that SIGNAL/SLOT is the first function declared in el.
            content = SIG_RE['fun_re'].search(el).groups()[0]
            content = content.strip()
            if not content.startswith(('"', "'")):
                # Unusual signal/slot declaration--not of the form 'name(args)'
                # Return the entire declaration string as the signal/slot name.
                print('WARNING: Invalid signal/slot declaration syntax:'+content)
                return [content]
            content = content.strip('\'"')

            slices = content.split('(')
            slices[0] = slices[0].lstrip()
            if len(slices) == 1:
                return slices

            return [slices[0]] + self.split_function(self.clean_signal_args(slices[1].replace(')', '')))

        # Signal/slot not declared with SIGNAL/SLOT nomenclature.
        # Return the entire string as the signal/slot name.
        return [el]

    def create_signal(self, lines, currentIdx, signal):
        """Adds the declaration of a new pyqtSignal class member.

        Args:
        lines -- the list of source code lines
        currentIdx -- index into lines list where use of signal was detected

        Returns:
        int -- number of additional lines inserted into lines list
        """
        module = signal.split('SIGNAL(')[0]
        signal = self.remove_signal_slot(signal)
        name = signal[0]

        line = lines[currentIdx]
        while not self.is_code_line(line) or not 'class ' in line:
            currentIdx -= 1
            line = lines[currentIdx]
        currentIdx += 1
        line = lines[currentIdx]
        while True:
            if self.is_code_line(line) and name in line:
                return 0
            if self.is_code_line(line) and not 'pyqtSignal' in line:
                break
            currentIdx += 1
            line = lines[currentIdx]

        indent = self.get_token_indent(line)
        if lines[currentIdx-1] == "\n":
            currentIdx -= 1
        if len(signal) == 1 or signal[0] == 'sslErrors':
            lines.insert(currentIdx, "%s = %spyqtSignal()\n" % (indent + name, module))
        else:
            type_str = ', '.join(signal[1:]).replace('::', '.')
            lines.insert(currentIdx, "%s = %spyqtSignal(%s)\n" % (indent + name, module, type_str))
        self._added_pyqtSignal = True

        currentIdx += 1
        line = lines[currentIdx]
        if line.lstrip().startswith('def '):
            lines.insert(currentIdx, "\n")
            return 2
        else:
            return 1

    def fix_connect(self, lines):
        """Refactor the pyqtSignal.connect()

        PyQt4 supports five versions of the connect() method:
            connect(SIP_QOBJECT, SIP_SIGNAL, SIP_QOBJECT, SIP_SLOT, Qt::ConnectionType=Qt::AutoConnection)
            connect(SIP_QOBJECT, SIP_SIGNAL, SIP_QOBJECT, SIP_SIGNAL, Qt::ConnectionType=Qt::AutoConnection)
            connect(SIP_QOBJECT, SIP_SIGNAL, SIP_SLOT, Qt::ConnectionType=Qt::AutoConnection)
            connect(SIP_QOBJECT, SIP_SIGNAL, SIP_SIGNAL, Qt::ConnectionType=Qt::AutoConnection)
            connect(SIP_QOBJECT, SIP_SIGNAL, SIP_PYCALLABLE, Qt::ConnectionType=Qt::AutoConnection)

        Args:
        lines -- source code
        """
        count = 0
        while count < len(lines):
            line = lines[count]

            if not self.is_code_line(line) or not '.connect(' in line:
                count += 1
                continue
            if not "SIGNAL(" in line:
                count += 1
                continue

            parts = line.split('.connect(')
            function = SIG_RE['fun_re'].search('('+parts[1])
            if function is None:
                count += 1
                continue

            # parse function arguments
            args = self.split_function(function.groups()[0])
            if len(args) < 3 or len(args) > 5:
                print('WARNING: Invalid connect() syntax:'+line)
                count += 1
                continue

            # parse signal argument
            if not "SIGNAL(" in args[1]:
                print('WARNING: Invalid connect() syntax:'+line)
                count += 1
                continue
            signal_obj = args[0]
            signal = self.remove_signal_slot(args[1])
            signal_fun = signal[0]
            signal_args = ''
            if len(signal) > 1 and signal[0] != 'sslErrors':
                signal_args = ', '.join(signal[1:]).replace('::', '.')

            # parse slot argument (which could be another signal)
            slot_obj = ''
            slot_args = ''
            slot_signal = ''
            if "SLOT(" in args[2] or "SIGNAL(" in args[2]:
                if "SIGNAL(" in args[2]:
                    slot_signal = args[2]
                    slot_obj = 'self'
                slot = self.remove_signal_slot(args[2])
                slot_fun = slot[0]
                if len(slot) > 1 and slot[0] != 'sslErrors':
                    slot_args = ', '.join(slot[1:]).replace('::', '.')
                other_args = ', '.join(args[3:])
            elif len(args) > 3 and ("SLOT(" in args[3] or "SIGNAL(" in args[3]):
                if "SIGNAL(" in args[3]:
                    slot_signal = args[3]
                slot_obj = args[2]
                slot = self.remove_signal_slot(args[3])
                slot_fun = slot[0]
                if len(slot) > 1 and slot[0] != 'sslErrors':
                    slot_args = ', '.join(slot[1:]).replace('::', '.')
                other_args = ', '.join(args[4:])
            else:
                slot_fun = args[2]
                other_args = ', '.join(args[3:])

            # put everything together
            indent = self.get_token_indent(line)
            lines[count] = indent + '%s.%s' % (signal_obj, signal_fun)
            if signal_args:
                lines[count] += '[%s]' % signal_args
            lines[count] += '.connect('
            if slot_obj:
                lines[count] += '%s.' % slot_obj
            lines[count] += '%s' % slot_fun
            if slot_signal and slot_args:
                lines[count] += '[%s]' % slot_args
            if other_args:
                lines[count] += ', %s' % other_args
            lines[count] += ')\n'

            if slot_signal:
                count += self.create_signal(lines, count, slot_signal)

            count += 1

    def fix_disconnect(self, lines):
        """Refactor the pyqtSignal.disconnect()

        PyQt4 supports three versions of the disconnect() method:
            disconnect(SIP_QOBJECT, SIP_SIGNAL, SIP_QOBJECT, SIP_SLOT)
            disconnect(SIP_QOBJECT, SIP_SIGNAL, SIP_QOBJECT, SIP_SIGNAL)
            disconnect(SIP_QOBJECT, SIP_SIGNAL, SIP_PYCALLABLE)
        PyQt4 does not support these versions of the disconnect() method (but this script does):
            connect(SIP_QOBJECT, SIP_SIGNAL, SIP_SLOT)
            connect(SIP_QOBJECT, SIP_SIGNAL, SIP_SIGNAL)

        Args:
        lines -- source code
        """
        for idx, line in enumerate(lines):
            if not self.is_code_line(line) or not '.disconnect(' in line:
                continue
            if not "SIGNAL(" in line:
                continue

            parts = line.split('.disconnect(')
            function = SIG_RE['fun_re'].search('('+parts[1])
            if function is None:
                continue

            # parse function arguments
            args = self.split_function(function.groups()[0])
            if len(args) < 3 or len(args) > 4:
                print('WARNING: Invalid disconnect() syntax:'+line)
                continue

            # parse signal argument
            if not "SIGNAL(" in args[1]:
                print('WARNING: Invalid disconnect() syntax:'+line)
                continue
            signal_obj = args[0]
            signal = self.remove_signal_slot(args[1])
            signal_fun = signal[0]
            signal_args = ''
            if len(signal) > 1 and signal[0] != 'sslErrors':
                signal_args = ', '.join(signal[1:]).replace('::', '.')

            # parse slot argument
            slot_obj = ''
            slot_args = ''
            slot_signal = ''
            if "SLOT(" in args[2] or "SIGNAL(" in args[2]:
                if "SIGNAL(" in args[2]:
                    slot_signal = args[2]
                    slot_obj = 'self'
                slot = self.remove_signal_slot(args[2])
                slot_fun = slot[0]
                if len(slot) > 1 and slot[0] != 'sslErrors':
                    slot_args = ', '.join(slot[1:]).replace('::', '.')
            elif len(args) > 3:
                if "SLOT(" not in args[3] and "SIGNAL(" not in args[3]:
                    print('WARNING: Invalid disconnect() syntax:'+line)
                    continue
                if "SIGNAL(" in args[3]:
                    slot_signal = args[3]
                slot_obj = args[2]
                slot = self.remove_signal_slot(args[3])
                slot_fun = slot[0]
                if len(slot) > 1 and slot[0] != 'sslErrors':
                    slot_args = ', '.join(slot[1:]).replace('::', '.')
            else:
                slot_fun = args[2]

            # put everything together
            indent = self.get_token_indent(line)
            lines[idx] = indent + '%s.%s' % (signal_obj, signal_fun)
            if signal_args:
                lines[idx] += '[%s]' % signal_args
            lines[idx] += '.disconnect('
            if slot_obj:
                lines[idx] += '%s.' % slot_obj
            lines[idx] += '%s' % slot_fun
            if slot_signal and slot_args:
                lines[idx] += '[%s]' % slot_args
            lines[idx] += ')\n'

    def fix_signal(self, lines):
        """
        clean decorator arguments
        """
        for idx, line in enumerate(lines):
            if '@pyqtSignal' in line:
                line = self.clean_signal_args(line)
                line = line.replace("'str'", "str").replace('"str"', 'str')
            lines[idx] = line

    def fix_slot(self, lines):
        """
        pyqtSignature decorator changed into pyqtSlot
        clean decorator arguments
        """
        for idx, line in enumerate(lines):
            line = line.replace('@pyqtSignature', '@pyqtSlot')
            if '@pyqtSlot' in line:
                line = self.clean_signal_args(line)
                line = line.replace("'str'", "str").replace('"str"', 'str')
            lines[idx] = line

    def fix_emit(self, lines):
        """
        Refactor the pyqtSignal.emit() old-style into a new-style line.
        Attempts also to create unexisting signals

        Args:
        lines -- the list of source code lines
        """
        count = 0
        while count < len(lines):
            line = lines[count]
            if self.is_code_line(line) and '.emit(' in line and 'SIGNAL(' in line:
                parts = line.split('.emit(')
                function = SIG_RE['fun_re'].search('('+parts[1])
                if function is not None:
                    args = self.split_function(function.groups()[0])
                    diff = diff_parenthesis(args[-1])
                    parenthesis = ')' * abs(diff)
                    if diff < 0:
                        li = args[-1].rsplit(')', abs(diff))
                        args[-1] = ''.join(li)
                    if len(args) == 2 and args[1] == '()':
                        args.pop()
                    lines[count] = '%s.%s.emit(%s)%s\n' % (parts[0], self.remove_signal_slot(args[0])[0], \
                                                           ', '.join(args[1:]), parenthesis)
                    count += self.create_signal(lines, count, args[0])
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
                    lines[count] = line.replace('trUtf8(', 'tr(')

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
                                    lines[count] = line.replace('.delta()', '.angleDelta().y()')
                            count += 1
            count += 1

    def fix_layoutmargin(self, lines):
        """Replace the QLayout method setMargin() by setContentsMargins()

        Args:
        lines -- the list of source code lines
        """
        layouts = []
        m_re = re.compile(r'[, =\(\-+]')
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
                        vals = ', '.join([val] * 4)
                        lines[idx] = '%s%s%s)\n' % (parts[0], news[0], vals)

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
        fixme = "# FIXME$ Ambiguous syntax for QDesktopServices, can't refactor it.\n"
        dsks = ['QDesktopServices()', 'QtGui.QDesktopServices()']
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
            if len(sub) < 2:
                count += 1
                continue

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
                    lines[count] = '%s = %s%s%s.%s)\n' % (sub[0].rstrip(), cls, method, cls, loc)
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
                 'QGraphicsItem',    # 'QGraphicsItemGroup',
                 'QGraphicsLineItem',
                 'QGraphicsPathItem',
                 'QGraphicsPixmapItem',
                 'QGraphicsPolygonItem',
                 'QGraphicsRectItem',
                 'QGraphicsSimpleTextItem',
                 'QGraphicsTextItem']
        for item in items:
            self.find_graphics_items(lines, item)

    def find_graphics_items(self, code, obj):
        fixme = "# FIXME$ Can't identify the QGraphicsScene in the arguments "\
                                                        "of the QGraphicsItem"
        count = 0
        while count < len(code):
            scene = False
            line = code[count]
            if not self.is_code_line(line) or line.lstrip().startswith(('import ', 'from ')):
                count += 1
                continue

            if obj in line:
                if self.is_class(line):
                    count = self.refactor_qgraphics_subclass(code, count, obj)
                    continue

                parts = line.split(obj)
                if parts[1].startswith('Group'):
                    obj += 'Group'
                    parts = line.split(obj)

                if not parts[1].lstrip().startswith('('):
                    # Not instantiated
                    count += 1
                    continue

                refs = parts[0].split('=')
                if len(refs) < 2:
                    # Unknown object
                    count += 1
                    continue
                ref = refs.pop(0)

                ind = self.get_token_indent(line)
                args = self.get_args(parts[1])
                scene, args = self.find_keyword('scene', args)
                if not scene:
                    # 0: ()
                    # 1: (parent)
                    # 1: (object)
                    if len(args) <= 1:
                        count += 1
                        continue

                    # 2: (*args, **kwargs)
                    # 2: (object, parent)
                    # 2: (parent, scene) -- possible problem
                    elif len(args) == 2:
                        if args[0] in ('*args', '* args') and args[1] in ('**kwargs', '** kwargs'):
                            # (*args, **kwargs)
                            count += 1
                            continue

                        elif args[-2] == 'None':
                            # (parent=None, scene)
                            scene = args.pop()

                        else:
                            parent_index = self.find_keyword_index('parent', args)
                            if parent_index == 0:
                                # (parent, scene)
                                scene = args.pop()

                            elif parent_index == 1:
                                # (object, parent)
                                count += 1
                                continue

                            else:
                                # (object, parent) or (parent, scene)
                                code.insert(count, '%s%s\n' % (ind, fixme))
                                count += 2
                                continue

                    # 3: (object, parent, scene)
                    elif len(args) == 3:
                        scene = args.pop()

                    # 4: (x, y, w, h)
                    # 5: (x, y, w, h, parent)
                    elif len(args) == 4 or len(args) == 5:
                        count += 1
                        continue

                    # 6: (x, y, w, h, parent, scene)
                    elif len(args) == 6:
                        scene = args.pop()

                    else:
                        code.insert(count, '%s%s\n' % (ind, fixme))
                        count += 2
                        continue

                code[count] = line.replace(parts[1], '(%s)\n' % ', '.join(args))
                if scene and scene != 'None':
                    string = '%s%s.addItem(%s)\n' % (ind, scene, ref.strip())
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

            scene, args = self.find_keyword('scene', args)
            if not scene:
                # 0: (self)
                # 1: (self, parent)
                # 1: (self, object)
                if len(args) <= 2:
                    return count + 1

                # 2: (self, *args, **kwargs)
                # 2: (self, object, parent)
                # 2: (self, parent, scene) -- possible problem
                elif len(args) == 3:
                    if args[1] in ('*args', '* args') and args[2] in ('**kwargs', '** kwargs'):
                        # (self, *args, **kwargs)
                        return count + 1

                    elif args[-2] == 'None':
                        # (self, parent=None, scene)
                        scene = args.pop()

                    else:
                        parent_index = self.find_keyword_index('parent', args)
                        if parent_index == 1:
                            # (self, parent, scene)
                            scene = args.pop()

                        elif parent_index == 2:
                            # (self, object, parent)
                            return count + 1

                        else:
                            # (self, object, parent) or (self, parent, scene)
                            lines.insert(count, '%s%s\n' % (ind, fixme))
                            return count + 2

                # 3: (self, object, parent, scene)
                elif len(args) == 4:
                    scene = args.pop()

                # 4: (self, x, y, w, h)
                # 5: (self, x, y, w, h, parent)
                elif len(args) == 5 or len(args) == 6:
                    return count + 1

                # 6: (self, x, y, w, h, parent, scene)
                elif len(args) == 7:
                    scene = args.pop()

                else:
                    lines.insert(count, '%s%s\n' % (ind, fixme))
                    return count + 2

            lines[count] = line.replace(parts[1], '(%s)\n' % ', '.join(args))
            if scene != 'None':
                count += 1
                lines.insert(count, '%sif %s is not None: %s.addItem(self)\n' % (ind, scene, scene))

            return count + 1

        return count + 1

    def get_args(self, string):
        # Remove the parenthesis
        string = string.strip()[1:-1]
        args = string.split(',')
        return [arg.strip() for arg in args]

    def find_keyword(self, keyword, args):
        keyarg = False
        for idx, arg in enumerate(args):
            if arg.startswith((keyword+'=', keyword+' =')):
                keyarg = args.pop(idx).split('=')[1].strip()
                break

            elif arg in (keyword, 'self.'+keyword):
                keyarg = args.pop(idx)
                break

        return keyarg, args

    def find_keyword_index(self, keyword, args):
        keyidx = -1
        for idx, arg in enumerate(args):
            if arg.startswith((keyword+'=', keyword+' =')) or arg in (keyword, 'self.'+keyword):
                keyidx = idx
                break

        return keyidx

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
                # TODO: Convert this to use regular expressions.
                # use PyQt5 since this method is called after change_import_lines
                line = line.replace('PyQt5.QtCore.QChar', 'QChar').replace('PyQt5.Qt.QChar', 'QChar')\
                           .replace('QtCore.QChar', 'QChar').replace('Qt.QChar', 'QChar')
                lines[idx] = line
                if '].connect(' in line or 'pyqtSignal(' in line:
                    line = line.replace("'QChar'", "QChar").replace('"QChar"', 'QChar')\
                               .replace("QChar", "'QChar'")
                    lines[idx] = line
                if 'QChar' in line.replace("'QChar'", "").replace('"QChar"', ''):
                    is_qchar = True

        if is_qchar:
            for idx in range_(len(lines)):
                if not self.is_code_line(lines[idx]) or lines[idx].lstrip().startswith(('import ', 'from ', '__')):
                    continue

                lines.insert(idx, "\n")

                ind = self.find_next_indent(lines[idx+1:])
                if not ind:
                    ind = "    "
                text = "try:\n%sQChar = unichr\nexcept NameError:\n"\
                       "%s# Python 3\n%sQChar = chr\n" % (ind, ind, ind)
                lines.insert(idx, text)

                break

    def fix_qstring(self, lines):
        """Replace QString() by unicode() for Python 2 and str() for Python 3.
           Also updates QString and QStringList usage as signal arguments.

        Args:
        code -- the list of source code lines
        """
        is_qstring = False
        is_qstring_list = False
        for idx, line in enumerate(lines):
            if self.is_code_line(line):
                # TODO: This does not handle QStringListModel properly.
                # TODO: Convert this to use regular expressions.
                # use PyQt5 since this method is called after change_import_lines
                line = line.replace('PyQt5.QtCore.QString', 'QString').replace('PyQt5.Qt.QString', 'QString')\
                           .replace('QtCore.QString', 'QString').replace('Qt.QString', 'QString')
                lines[idx] = line
                if '].connect(' in line or 'pyqtSignal(' in line:
                    line = line.replace("'QString'", "QString").replace('"QString"', 'QString')\
                               .replace("'QStringList'", "QStringList").replace('"QStringList"', 'QStringList')\
                               .replace("QString", "'QString'").replace("'QString'List", "'QStringList'")\
                               .replace("'QStringList'Model", "QStringListModel")
                    lines[idx] = line
                if 'QString' in line.replace('QStringListModel', '').replace('QStringList', '')\
                                    .replace("'QString'", "").replace('"QString"', ''):
                    is_qstring = True
                if 'QStringList' in line.replace('QStringListModel', '')\
                                        .replace("'QStringList'", "").replace('"QStringList"', ''):
                    is_qstring_list = True

        if is_qstring or is_qstring_list:
            for idx in range_(len(lines)):
                if not self.is_code_line(lines[idx]) or lines[idx].lstrip().startswith(('import ', 'from ', '__')):
                    continue

                lines.insert(idx, "\n")

                if is_qstring_list:
                    text = "QStringList = list\n"
                    lines.insert(idx, text)

                if is_qstring:
                    ind = self.find_next_indent(lines[idx+1:])
                    if not ind:
                        ind = "    "
                    text = "try:\n%sQString = unicode\nexcept NameError:\n"\
                           "%s# Python 3\n%sQString = str\n" % (ind, ind, ind)
                    lines.insert(idx, text)

                break

    def fix_qglobal(self, lines):
        """Replace calls to qInstallMsgHandler() with calls to qInstallMessageHandler().

        Args:
        code -- the list of source code lines
        """
        for idx, line in enumerate(lines):
            if self.is_code_line(line):
                lines[idx] = line.replace('qInstallMsgHandler(', 'qInstallMessageHandler(')

    def fix_qvariant(self, lines):
        """Remove calls to obsolete QVariant conversion functions.

        Args:
        code -- the list of source code lines
        """
        for idx, line in enumerate(lines):
            if self.is_code_line(line):
                for method in QVARIANT_OBSOLETE_METHODS:
                    line = line.replace('.'+method+'()', '')
                lines[idx] = line

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

    def replace_qApp(self, lines):
        """Replace qApp usage with QApplication.static_method() or QApplication.instance().method().

        Args:
        lines -- source code
        """
        for idx, line in enumerate(lines):
            if not self.is_code_line(line) or not 'qApp' in line:
                continue

            if line.lstrip().startswith(('import ', 'from ')):
                line = self.replace_module(line, 'qApp', 'QApplication')

            else:
                # use QtWidgets.qApp since this method is called after change_module_name
                for func in QAPP_STATIC_METHODS:
                    line = re.sub(r'(\A|[^a-zA-Z0-9_.\'"]|Qt\.|QtWidgets\.)qApp\.'+func+r'(\Z|[^a-zA-Z0-9_])',
                                  r'\1QApplication.'+func+r'\2', line)

                line = re.sub(r'(\A|[^a-zA-Z0-9_.\'"]|Qt\.|QtWidgets\.)qApp(\Z|[^a-zA-Z0-9_])',
                              r'\1QApplication.instance()\2', line)

            lines[idx] = line

    def replace_classnames(self, lines):
        """Rename some classe's names.

        QMatrix to QTransform
        QIconEngineV2 to QIconEngine

        Args:
        lines -- source code
        """
        # TODO: Convert this to use regular expressions like in replace_qApp above,
        #       so that only the appropriate instances of olds are converted.
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
        if not line.strip() or self.is_comment(line) or self.is_string(line) or self.is_docstring(line):
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

    def is_string(self, line):
        """Returns True if a line is a string.

        Args:
        line -- the line code
        """
        return line.lstrip().startswith(('"', "'"))

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
        start -- the word where the count begins
        end -- the word where the count finishes

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

    def find_closing_parenthesis(self, line, prefix=None):
        """Find the closing parenthesis according to a given opening parenthesis.

        Args:
        line -- one logical line of code
        prefix -- the word that precedes the opening parenthesis

        Returns:
        tuple(ocol, ccol) where ocol is the column of the opening parenthesis
                          and ccol is the column of the closing parenthesis
        """
        begin = not prefix
        count = 0
        ocol = ccol = 0
        tokens = tokenize.generate_tokens(StringIO(line).readline)
        for typ, st, bg, _, _ in tokens:
            if typ == tokenize.NL:
                if not begin:
                    ocol += bg[1]+1
                ccol += bg[1]+1

            elif prefix and st == prefix:
                begin = True
                if prefix == '(':
                    count += 1
                    ocol += bg[1]

            elif begin and st == '(':
                if not count:
                    ocol += bg[1]
                count += 1

            elif count and st == ')':
                count -= 1
                if not count:
                    return ocol, ccol+bg[1]

        return len(line), len(line)

    def remove_fromUtf8(self, lines):
        """Remove calls to QString.fromUtf8 often redefined as _fromUtf8

        Args:
        lines -- the list of source code lines
        """
        count = 0
        while count < len(lines):
            line = lines[count]

            if not self.is_code_line(line):
                count += 1
                continue

            # remove the definition of the _fromUtf8 function or redefine it
            if line.strip() == '_fromUtf8 = QtCore.QString.fromUtf8':
                if count > 0 and lines[count-1].strip() == 'try:' and \
                        count+1 < len(lines) and lines[count+1].strip() == 'except AttributeError:':
                    if count+2 < len(lines) and lines[count+2].strip() == '_fromUtf8 = lambda s: s':
                        i, j = count-1, count+3
                        if j < len(lines) and lines[j].strip() == '':
                            j += 1
                        lines[i:j] = []
                        count -= 1
                        continue
                    elif count+3 < len(lines) and lines[count+2].strip() == 'def _fromUtf8(s):' and \
                            lines[count+3].strip() == 'return s':
                        i, j = count-1, count+4
                        if j < len(lines) and lines[j].strip() == '':
                            j += 1
                        lines[i:j] = []
                        count -= 1
                        continue
                else:
                    indent = self.get_token_indent(line)
                    lines[count] = indent + '_fromUtf8 = lambda s: s\n'
                    continue

            line = line.replace("PyQt4.QtCore.QString.fromUtf8(", "_fromUtf8(")\
                       .replace("PyQt4.Qt.QString.fromUtf8(", "_fromUtf8(")\
                       .replace("QtCore.QString.fromUtf8(", "_fromUtf8(")\
                       .replace("Qt.QString.fromUtf8(", "_fromUtf8(")\
                       .replace("QString.fromUtf8(", "_fromUtf8(")
            while True:
                open_idx, close_idx = self.find_closing_parenthesis(line, '_fromUtf8')
                if open_idx >= len(line):
                    break
                line = line[:open_idx-9] + line[open_idx+1:close_idx] + line[close_idx+1:]

            lines[count] = line
            count += 1

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

    def clean_signal_args(self, signal):
        if self._pyqt5:
            signal = signal.replace('const char*', 'str').replace('const char *', 'str')
        else:
            signal = signal.replace('const char*', 'const_char_star_arg')\
                           .replace('const char *', 'const_char_space_star_arg')
        signal = signal.replace(' const ', '').replace('const ', '')
        signal = signal.replace(' * ', '').replace(' *', '').replace('* ', '').replace('*', '')
        signal = signal.replace(' & ', '').replace(' &', '').replace('& ', '').replace('&', '')
        signal = signal.replace("PyQt_PyObject", "'PyQt_PyObject'")
        if self._pyqt5:
            # TODO: Convert this to use regular expressions.
            signal = signal.replace("PyQt4.QtCore.QString", "QString").replace("PyQt4.Qt.QString", "QString")\
                           .replace("QtCore.QString", "QString").replace("Qt.QString", "QString")\
                           .replace("QString", "'QString'").replace("'QString'List", "'QStringList'")\
                           .replace("'QStringList'Model", "QStringListModel")
        else:
            signal = signal.replace('const_char_star_arg', '"const char*"')\
                           .replace('const_char_space_star_arg', '"const char *"')
        return signal

    def clean_signal(self, signal):
        signal = self.clean_signal_args(signal)
        signal = signal.replace('()', '')
        signal = signal.replace('(', '[').replace(')', ']')
        return signal

    def replace_module(self, line, old_mod, new_mod=None):
        # TODO: Convert this to use regular expressions.
        if new_mod:
            line = line.replace(','+old_mod+',', ','+new_mod+',')\
                       .replace(', '+old_mod+',', ', '+new_mod+',')\
                       .replace(','+old_mod+'\n', ','+new_mod+'\n')\
                       .replace(', '+old_mod+'\n', ', '+new_mod+'\n')\
                       .replace(','+old_mod+'\\', ','+new_mod+'\\')\
                       .replace(','+old_mod+' \\', ','+new_mod+' \\')\
                       .replace(', '+old_mod+'\\', ', '+new_mod+'\\')\
                       .replace(', '+old_mod+' \\', ', '+new_mod+' \\')\
                       .replace(' '+old_mod+', ', ' '+new_mod+', ')\
                       .replace(' '+old_mod+',', ' '+new_mod+',')\
                       .replace(' '+old_mod+'\n', ' '+new_mod+'\n')\
                       .replace(' '+old_mod+'\\', ' '+new_mod+'\\')\
                       .replace(' '+old_mod+' \\', ' '+new_mod+' \\')
        else:
            line = line.replace(','+old_mod+',', ',')\
                       .replace(', '+old_mod+',', ',')\
                       .replace(','+old_mod+'\n', '\n')\
                       .replace(', '+old_mod+'\n', '\n')\
                       .replace(','+old_mod+'\\', '\\')\
                       .replace(','+old_mod+' \\', ' \\')\
                       .replace(', '+old_mod+'\\', '\\')\
                       .replace(', '+old_mod+' \\', ' \\')\
                       .replace(' '+old_mod+', ', ' ')\
                       .replace(' '+old_mod+',', ' ')\
                       .replace(' '+old_mod+'\n', '\n')\
                       .replace(' '+old_mod+'\\', '\\')\
                       .replace(' '+old_mod+' \\', ' \\')

        # Remove empty in between lines
        return L_SEP.join(l for l in line.split(L_SEP) if l.strip()) + L_SEP

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
                news.append(txt.replace('PyQt4', 'PyQt5') + '.QtCore import QStandardPaths\n')
                self.modified['QStandardPaths'] = False

        while count < len(lines):
            line = lines[count]

            if not self.is_code_line(line):
                news.append(line)
                count += 1
                continue

            ls_line = line.lstrip()
            if line.lstrip().startswith(('import ', 'from ')):
                line = line.rstrip() + '\n'
                if self._added_pyqtSignal:
                    line = self.replace_module(line, 'SIGNAL', 'pyqtSignal')
                else:
                    line = self.replace_module(line, 'SIGNAL', '')

                line = self.replace_module(line, 'SLOT', '')

                if self._pyqt5:
                    line = self.replace_module(line, 'QStringList', '')
                    line = self.replace_module(line, 'QString', '')

                if line.strip() == 'import' or line.rstrip().endswith(' import'):
                    count += 1
                    continue

            if not self._pyqt5:
                news.append(line)
                count += 1
                continue

            if ls_line.startswith('from PyQt4.QtCore ') and self.modified['QStandardPaths']:
                news.append(line.replace('PyQt4', 'PyQt5').rstrip() + ', QStandardPaths\n')
                self.modified['QStandardPaths'] = False

            elif ls_line.startswith('from PyQt4.QtCore ') and 'QChar' in line:
                elems = [c.strip() for c in line[25:].split(',')]
                elems.remove('QChar')
                if elems:
                    news.append('from PyQt5.QtCore import ' + ', '.join(elems) + '\n')

            elif ls_line.startswith('from PyQt4 import '):
                line = self.refactor_modules_import(line)
                if line:
                    txt = self.reindent_import_line(line)
                    news.append(txt)
                    set_qstandardpaths(line.split(' import ')[0])

            elif ls_line.startswith('from PyQt4.Qt import '):
                parts = line.split('import ')
                core, gui, wdg, pr, md, ogl, cm = self.sort_qt_classes(parts[1])
                if core:
                    stcore = "".join([parts[0].replace('PyQt4.Qt ',
                                'PyQt5.QtCore import '), ', '.join(core)])
                    txt = self.reindent_import_line(stcore)
                    news.append(txt)
                if gui:
                    stgui = "".join([parts[0].replace('PyQt4.Qt ',
                                'PyQt5.QtGui import '), ', '.join(gui)])
                    txt = self.reindent_import_line(stgui)
                    news.append(txt)
                if wdg:
                    stwdg = "".join([parts[0].replace('PyQt4.Qt ',
                                'PyQt5.QtWidgets import '), ', '.join(wdg)])
                    txt = self.reindent_import_line(stwdg)
                    news.append(txt)
                    self._has_qtwidget_import = True
                if pr:
                    stpr = "".join([parts[0].replace('PyQt4.Qt ',
                                'PyQt5.QtPrintSupport import '), ', '.join(pr)])
                    txt = self.reindent_import_line(stpr)
                    news.append(txt)
                if md:
                    stmd = "".join([parts[0].replace('PyQt4.Qt ',
                                'PyQt5.QtMultimedia import '), ', '.join(md)])
                    txt = self.reindent_import_line(stmd)
                    news.append(txt)
                if ogl:
                    stogl = "".join([parts[0].replace('PyQt4.Qt ',
                                'PyQt5.QtOpenGL import '), ', '.join(ogl)])
                    txt = self.reindent_import_line(stogl)
                    news.append(txt)
                if cm:
                    txt = L_SEP.join(cm) + L_SEP
                    news.append(txt)
                set_qstandardpaths(line.split('.Qt')[0])

            elif ls_line.startswith('from PyQt4.QtGui '):
                parts = line.split('import ')
                core, gui, wdg, pr, md, cm = self.sort_qtgui_classes(parts[1])
                if core:
                    stcore = "".join([parts[0].replace('PyQt4.QtGui ',
                                'PyQt5.QtCore import '), ', '.join(core)])
                    txt = self.reindent_import_line(stcore)
                    self._has_qtwidget_import = True
                    news.append(txt)
                if gui:
                    stgui = "".join([parts[0].replace('PyQt4', 'PyQt5'),
                                'import ', ', '.join(gui)])
                    txt = self.reindent_import_line(stgui)
                    news.append(txt)
                if wdg:
                    stwdg = "".join([parts[0].replace('PyQt4.QtGui ',
                                'PyQt5.QtWidgets import '), ', '.join(wdg)])
                    txt = self.reindent_import_line(stwdg)
                    self._has_qtwidget_import = True
                    news.append(txt)
                if pr:
                    stpr = "".join([parts[0].replace('PyQt4.QtGui ',
                                'PyQt5.QtPrintSupport import '), ', '.join(pr)])
                    txt = self.reindent_import_line(stpr)
                    news.append(txt)
                if md:
                    stmd = "".join([parts[0].replace('PyQt4.QtGui ',
                                'PyQt5.QtMultimedia import '), ', '.join(md)])
                    txt = self.reindent_import_line(stmd)
                    news.append(txt)
                if cm:
                    txt = L_SEP.join(cm) + L_SEP
                    news.append(txt)
                set_qstandardpaths(line.split('.QtGui')[0])

            elif ls_line.startswith('from PyQt4.QtWebKit '):
                parts = line.split('import ')
                wb, wdg = self.sort_qtwebkit_classes(parts[1])
                if wb:
                    chain = "".join([parts[0].replace('PyQt4', 'PyQt5'),
                                'import ', ', '.join(wb)])
                    txt = self.reindent_import_line(chain)
                    news.append(txt)
                if wdg:
                    chain = "".join([parts[0].replace('PyQt4.QtWebKit',
                                'PyQt5.QtWebKitWidgets'), 'import ', ', '.join(wdg)])
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
        parts = line.split('import ')
        chain = parts[0].replace('PyQt4', 'PyQt5') + 'import '
        end = parts[1].replace('(', '').replace(')', '').replace('\\', '')
        modules = set([name.strip() for name in end.split(',')
                       if name.strip()])

        if 'QtGui' in modules and not self.modified['QtGui']:
            modules.remove('QtGui')

        if self.modified['QtCore']:
            modules.add('QtCore')

        if self.modified['QtWidgets']:
            modules.add('QtWidgets')
            self._has_qtwidget_import = True

        if 'QtWebKit' in modules and not self.modified['QtWebKit']:
            modules.remove('QtWebKit')

        if self.modified['QtWebKitWidgets']:
            modules.add('QtWebKitWidgets')

        if self.modified['QtMultimedia'] and not 'QtMultimedia' in modules:
            modules.add('QtMultimedia')

        if self.modified['QtPrintSupport']:
            modules.add('QtPrintSupport')

        if not modules:
            return None

        modules = list(modules)
        modules.sort()
        return chain + ', '.join(modules)

    def sort_qtgui_classes(self, chain):
        """Sort the classes from a QtGui import line.

        Args:
        chain -- the classe's names in one line

        Returns:
        Six class lists:
            QtCore, QtGui, QtWidgets, QtPrintSupport, QtMultimedia, comments
        """
        names = [line.strip(',') for line in chain.split(L_SEP)]
        core = []
        gui = []
        widgets = []
        printer = []
        media = []
        cm = []
        for name in names:
            name = name.replace('\\', '')
            cls = name.replace('(', '').replace(')', '').strip()
            if not cls:
                continue

            if self.is_comment(cls):
                cm.append(cls)

            elif cls in CLASSES['QtCore']:
                core.append(cls)

            elif cls in CLASSES['QtWidgets']:
                widgets.append(cls)

            elif cls in CLASSES['QtMultimedia']:
                media.append(cls)

            elif cls in CLASSES['QtPrintSupport']:
                printer.append(cls)

            else:
                if cls == 'QIconEngineV2':
                    cls = 'QIconEngine'
                elif cls == 'QMatrix':
                    cls = 'QTransform'
                gui.append(cls)

        return core, gui, widgets, printer, media, cm

    def sort_qt_classes(self, chain):
        """
        Sort the classes from a qt import line

        Args:
        chain -- the classe's names in one line

        Returns:
        Seven class lists:
            QtCore, QtGui, QtWidgets, QtPrintSupport, QtMultimedia,
            QtOpenGL, comments
        """
        core, old_gui, widgets, printer, media, cm = self.sort_qtgui_classes(chain)
        gui = []
        opengl = []
        for cls in old_gui:
            if cls in CLASSES['QtOpenGL']:
                opengl.append(cls)
            else:
                gui.append(cls)
        return core, gui, widgets, printer, media, opengl, cm

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

        The lines have maximum 80 characters and the indentations are fixed at
        the column of the first open parenthesis of the first line.

        Args:
        line -- the original line

        Returns:
        Multiline
        """
        if len(line) < 81:
            return line + '\n'

        begin, end = line.split('import ')
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
        with open(self.dest, 'wb') as outf:
            outf.write(''.join(lines).replace('\n', L_SEP).encode(self.tools.encoding))

        mode = os.stat(self.source).st_mode
        os.chmod(self.dest, mode)

    def print_(self, msg):
        sys.stdout.write('%s\n' % msg)
        if self.log:
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
            return None

        return self.get_content(filename)

    def get_content(self, filename):
        if PY_VERS < 3:
            try:
                with open_(filename, "rU", encoding=self.encoding) as inf:
                    content = inf.read()
            except (IOError, UnicodeDecodeError) as why:
                self.last_error = why
                return None

        else:
            try:
                with open(filename, "r", encoding=self.encoding) as inf:
                    content = inf.read()
            except (IOError, UnicodeDecodeError) as why:
                self.last_error = why
                return None

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
            sys.stdout.write("Can't read the file `%s`\nReason: %s\n" % (path, why))
            return None

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
        comment = re.compile(r"coding[:=]\s*([-\w.]+)")
        try:
            string = chain.decode('ascii')
        except UnicodeDecodeError:
            return None

        matches = comment.findall(string)
        if not matches:
            return None

        codings = ("latin-1", "iso-8859-1", "iso-latin-1")
        codings_with_dash = tuple(coding+'-' for coding in codings)
        enc = matches[0][:12].lower().replace("_", "-")
        if enc == "utf-8" or enc.startswith("utf-8-"):
            encoding = "utf-8"

        elif enc in codings or enc.startswith(codings_with_dash):
            encoding = "iso-8859-1"

        else:
            sys.stdout.write("Non-standard encoding: %s\n" % enc)
            encoding = enc

        try:
            codec = lookup(encoding)
        except LookupError:
            sys.stdout.write("Can't read the encoding: %s\n" % encoding)
            return None

        if bom:
            if codec.name != 'utf-8':
                sys.stdout.write("Inconsistant encoding: %s\n" % encoding)
                return None
            encoding += '-sig'

        return encoding

    def get_code_lines(self, filename):
        count = 0
        source = self.read_python_source(filename)
        if source is None:
            # error reading input file
            return None

        if not source[-1]:
            source.pop()

        if not source:
            #self.last_error = 'File is empty'
            #return None
            return []

        orig = ['%s\n' % l for l in source]
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
        """Returns the line nummer where a logical line ends.

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

        new = True
        com = False
        tokens = tokenize.generate_tokens(src)
        # tokens = (token type, token string, (srow, scol), (erow, ecol), line)
        try:
            for typ, _, _, end, ln in tokens:
                if typ == tokenize.ENDMARKER:
                    # End of file
                    yield end[0]

                elif typ == tokenize.NEWLINE:
                    # End of logical line
                    new = True
                    yield end[0]

                elif typ == tokenize.COMMENT and new:
                    # One line comment
                    com = True
                    new = True
                    yield end[0]

                elif typ == tokenize.NL:
                    # End of physical line
                    if com:
                        com = False
                        new = True
                    elif not ln.strip() and new:
                        # Empty line
                        new = True
                        yield end[0]
                    else:
                        new = False

                elif typ == tokenize.ERRORTOKEN:
                    # Error token
                    raise Exception('Error token encountered')

                else:
                    new = False

        except Exception as why:
            sys.stdout.write('Except: %s\nLine: %s\n%s' %(why, end, ln))
            self.last_error = why
            yield False

        finally:
            inf.close()


class Main(object):
    def __init__(self, args):
        self.copied = {}
        self.path = None
        self.nosubdir = False
        self.followlinks = False
        self.destdir = None
        self.write_diff = False
        self.write_diffs = False
        self.filename_diff = False
        self.nopyqt5 = False
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
        parser.add_argument("--followlinks", action="store_true",
                        help="Visit directories pointed to by symlinks."
                        "  Default: False")
        parser.add_argument("-o", nargs=1, help="The name of the generated "
                        "file or directory if path is a directory."
                        "  Default: path_PyQt5 (path_PyQt4 if --nopyqt5)")
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
        parser.add_argument("--nolog", action="store_true",
                        help="Do not create a log file."
                        "  Default: False")
        parser.add_argument("--nopyqt5", action="store_true",
                        help="Only perform updates that are compatable with PyQt4."
                        "  Default: False")
        arg = parser.parse_args()

        if arg.path:
            self.path = self.check_path(arg.path)
            if not self.path:
                sys.exit()

        if arg.nosubdir:
            self.nosubdir = True

        if arg.followlinks:
            self.followlinks = True

        if arg.diff:
            self.write_diff = arg.diff

        if arg.diffs:
            self.write_diffs = True

        if arg.nopyqt5:
            self.nopyqt5 = True

        if arg.o:
            self.destdir = self.check_path(arg.o[0], True)
            if not self.destdir:
                sys.exit()
        else:
            self.destdir = self.path

        if arg.nolog:
            self.log = None
        else:
            self.log = 'pyqt4_to_pyqt4.log' if self.nopyqt5 else 'pyqt4_to_pyqt5.log'
            date = datetime.now().strftime("%A %d. %B %Y %H:%M")
            self.print_('**  %s  %s  **\nArgs: %s\n' % (self.log, date, sys.argv))

        self.prepare_changes(self.followlinks)

    def is_python_file(self, path):
        """Checks if the given path is a Python file or not.

        Args:
        path -- path to file

        Returns:
        bool -- True if the path is a Python file and False otherwise
        """

        # check if file is a regular file
        mode = os.stat(path).st_mode
        if not stat.S_ISREG(mode):
            return False

        # check if file has a Python extension
        ext = os.path.splitext(path)[1]
        if ext in PYEXT:
            return True

        # check if file is executable and contains a Python shebang
        if mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
            with open(path, 'r') as inf:
                line = inf.readline().strip()
                if line in PYSHEBANG:
                    return True

        return False

    def prepare_changes(self, followlinks=False):
        ver = "PyQt4" if self.nopyqt5 else "PyQt5"

        if os.path.isdir(self.path):
            if self.destdir == self.path:
                self.destdir = self.path + "_" + ver

            self.copy_dir(self.destdir, self.path, followlinks=followlinks)
            self.set_diff_option('dir')
            self.process_from_dir(self.destdir, followlinks=followlinks)

        elif os.path.isfile(self.path):
            if not self.is_python_file(self.path):
                # Assume this is a list of files
                files, subdirs = self.read_filenames(self.path)
                if self.destdir == self.path:
                    self.destdir = "__" + ver + "__"

                self.copy_files(self.destdir, subdirs, files)
                self.set_diff_option('dir')
                self.process_from_dir(self.destdir, followlinks=followlinks)

            else:
                if self.destdir == self.path:
                    f, e = os.path.splitext(self.path)
                    self.destdir = "".join([f, "_"+ver, e])

                if self.write_diff:
                    self.set_diff_option('file')
                cnv = PyQt4ToPyQt5(self.path, self.destdir, self.log, self.nopyqt5)
                cnv.setup()
                self.write_diff_file(self.destdir, self.path)

    def process_from_dir(self, fld, followlinks=False):
        self.print_('Beginning into: %s\n' % fld)
        for root, _, files in os.walk(fld, followlinks=followlinks):
            files.sort()
            for f in files:
                fname = os.path.join(root, f)
                cnv = PyQt4ToPyQt5(fname, fname, self.log, self.nopyqt5)
                cnv.setup()
                self.write_diff_file(fname)

    def copy_dir(self, dest, orig, followlinks=False):
        self.copied = {}
        try:
            os.makedirs(dest)
        except Exception as why:
            sys.stdout.write("Can't create the dir: `%s`\nReason: %s\n" % (dest, why))
            sys.exit()

        if self.nosubdir:
            files = glob.glob(os.path.join(orig, '*.py'))
            for f in files:
                shutil.copy(f, dest)
                self.copied[dest] = f
            return

        for root, dirs, files in os.walk(orig, followlinks=followlinks):
            dirs[:] = [d for d in dirs if d not in ('__pycache__', '.git')]

            target = root.replace(orig, dest)
            for name in dirs:
                os.makedirs(os.path.join(target, name))

            for name in files:
                src = os.path.join(root, name)
                if self.is_python_file(src):
                    cp = os.path.join(target, name)
                    shutil.copy(src, cp)
                    self.copied[cp] = src

    def read_filenames(self, path):
        try:
            with open(path, 'r') as inf:
                files = [f.strip() for f in inf.readlines()]
        except IOError as why:
            sys.stdout.write("Can't read the file: `%s`\nReason: %s\n" % (path, why))
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
                sys.stdout.write("Can't create the dir: `%s`\nReason: %s\n" % (dest, why))
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
                sys.stdout.write('No such file or directory: `%s`\n' % path)
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

        cmd = ['diff', '-u', orig, dest]
        with open(diffname, 'a') as outf:
            reply = subprocess.Popen(cmd, stdout=subprocess.PIPE)
            outf.write(str(reply.communicate()[0]))

    def print_(self, msg):
        if self.log:
            with open(self.log, 'a') as outf:
                outf.write('%s\n' % msg)

if __name__ == '__main__':
    main = Main(sys.argv)
