.PHONY: release install files test docs prepare publish

all:
    @echo "make release - prepares a release and publishes it"
    @echo "make test - run tox"
    @echo "make publish - upload to pypi"
    @echo "make install - install on local system"
    @echo "make files - update changelog and todo files"
    @echo "make docs - build docs"
    @echo "prepare - prepare module for release"

release: test docs prepare publish

dev: instdev docs

instdev:
    python setup.py develop

install:
    python setup.py install

files:
    grep '# TODO' -rn * --exclude-dir=docs --exclude-dir=build --exclude=TODO.md | sed 's/: \+#/:    # /g;s/:#/:    # /g' | sed -e 's/^/- /' | grep -v Makefile > TODO.md
    git log --oneline --decorate --color > CHANGELOG

test:
    tox

docs:
    cd docs && make html
    pandoc README.md -f markdown -t rst -s -o README.rst

prepare:
    python scripts/make-release.py

publish:
    python setup.py sdist upload