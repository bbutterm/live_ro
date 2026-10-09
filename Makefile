.PHONY: check
check:
	PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -t . -v
