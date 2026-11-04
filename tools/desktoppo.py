#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Desktop-file translation helper (adapted from dnfdragora's tools/desktoppo.py).

Generates ``locale/desktop/manaradix_desktop.pot`` from the translatable
keys (Name, GenericName, Comment, Keywords) of every ``*.desktop`` file in
the given directory, merges it into ``locale/desktop/<lang>.po``, and
rewrites the desktop files with the translated ``Key[lang]=`` lines.

Run from the repository root, after ``tx pull`` in ``locale/desktop``::

    python3 tools/desktoppo.py -d data/desktop

Requires ``polib`` (developer tool only, never run at package build time).
"""

import errno, glob, polib, re, os, getopt, sys
from time import strftime

PODIR = 'locale/desktop'
POTFILE = os.path.join(PODIR, 'manaradix_desktop.pot')


def usage():
    print('\nUsage: python %s [OPTION]' % os.path.basename(sys.argv[0]))
    print('        generate pot catalogs and updates po files for desktop resources in the specified directory')
    print('Options: -h, --help                                    : usage')
    print('         -d <directory>, --directory <directory> : directory with desktop files')
    sys.exit(2)

try:
    opts, args = getopt.getopt(sys.argv[1:], "hd:", ["help", "directory="])
except getopt.GetoptError:
    usage()  # print help information and exit

directory = '.'
for o, a in opts:
    if o in ("-h", "--help"):
        usage()
    if o in ("-d", "--directory"):
        directory = a

directory = directory.rstrip('/')

if (directory != '') and (os.path.isdir(directory) == False):
    sys.exit('Specified directory does not exist')

# Find all desktop files
files = []
for rootdir, dirnames, filenames in os.walk(directory):
    files.extend(sorted(glob.glob(os.path.join(rootdir, "*.desktop"))))

# Define Templates and po directory name
messagetemplate = r'(?<=\n)(Name=.*?\n|Comment=.*?\n|GenericName=.*?\n|Keywords=.*?\n)'
mpattern = re.compile(messagetemplate, re.DOTALL)
translationtemplate = r'(?<=\n)(Name\[.*?\n|Comment\[.*?\n|GenericName\[.*?\n|Keywords\[.*?\n)'
tpattern = re.compile(translationtemplate, re.DOTALL)
podir = PODIR

# Write POT file
pot = polib.POFile('', check_for_duplicates=True)
potcreationtime = strftime('%Y-%m-%d %H:%M%z')
pot.metadata = {
    'Project-Id-Version': 'manaradix desktop files translation',
    'Report-Msgid-Bugs-To': 'i18n-discuss@ml.mageia.org',
    'POT-Creation-Date': potcreationtime,
    'PO-Revision-Date': 'YEAR-MO-DA HO:MI+ZONE',
    'Last-Translator': 'FULL NAME <EMAIL@ADDRESS>',
    'Language-Team': 'LANGUAGE <LL@li.org>',
    'MIME-Version': '1.0',
    'Content-Type': 'text/plain; charset=UTF-8',
    'Content-Transfer-Encoding': '8bit',
}

for langfile in files:
    # Create localization directories if needed
    try:
        os.makedirs(podir)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise

    # Open desktop file with explicit UTF-8 encoding
    with open(langfile, "r", encoding="utf-8") as f:
        text = f.read()

    # Parse contents and add them to POT
    for mblock in mpattern.findall(text):
        mblock_stripped = mblock.strip('\n')
        message_comment, message_id = mblock_stripped.split('=', 1)
        potentry = polib.POEntry(
            msgctxt=message_comment,
            msgid=message_id,
            msgstr='',
            occurrences=[(langfile, '')]
        )
        if message_id != '':
            try:
                pot.append(potentry)
            except ValueError:
                print('The entry already exists')

pot.save(POTFILE)

# Merge translations
for pofile in glob.glob(os.path.join(podir, '*.po')):
    pofilename = pofile
    po = polib.pofile(pofilename)
    po.merge(pot)
    po.save(pofilename)

for langfile in files:
    # Open desktop file
    with open(langfile, "r", encoding="utf-8") as deskfile:
        text = deskfile.read()

    for transblock in tpattern.findall(text):
        text = text.replace(transblock, '')

    # Parse PO files
    for pofile in sorted(glob.glob(os.path.join(podir, '*.po')), reverse=True):
        lang = os.path.basename(pofile)[:-3]
        pofilename = pofile
        po = polib.pofile(pofilename)
        for entry in po.translated_entries():
            if entry.msgid in text:
                origmessage = '\n' + entry.msgctxt + '=' + entry.msgid + '\n'
                origandtranslated = '\n' + entry.msgctxt + '=' + entry.msgid + '\n' + entry.msgctxt + '[' + lang + ']=' + entry.msgstr + '\n'
                text = text.replace(origmessage, origandtranslated)

    with open(langfile, "w", encoding="utf-8") as deskfile:
        deskfile.write(text)
