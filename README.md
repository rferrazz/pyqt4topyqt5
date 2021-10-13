pyqt4topyqt5
============

pyqt4 -> pyqt5

## Usage
```bash
// install the tool
pip install .

pyqt4topyqt5 [-h] [--nosubdir] [--followlinks] [-o O]
             [--diff [DIFF]] [--diffs] [--nolog] [--nopyqt5]
             path
```

Basic example: porting the content of `pyqt4app` to pyqt5 in the directory `pyqt5app`:
```bash
pyqt4topyqt5 pyqt4app -o pyqt5app
```
