from distutils.core import setup, Extension

module = Extension('squired', sources = ['src/squiredmodule.c'])
setup(name = 'squire test', version = '1.0', ext_modules = [module])
