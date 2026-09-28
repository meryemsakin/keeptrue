.PHONY: install test demo fixtures svg build clean

install:      ## editable install with dev deps
	pip install -e ".[dev]"

test:         ## run the test suite
	pytest -q

demo:         ## run the hero command
	keeptrue demo

fixtures:     ## regenerate the bundled demo runs (deterministic)
	python -m keeptrue._fixtures.build_demo

svg:          ## refresh docs/demo.svg from the demo
	python scripts/render_demo_svg.py

build:        ## build sdist + wheel
	python -m build

clean:
	rm -rf dist build *.egg-info src/*.egg-info
