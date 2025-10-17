"""
README

Production Plan API (Python)

This scripts mplements a REST API exposing POST /productionplan which accepts a payload in JSON format (given the examples) and returns a JSON array of production values.

Features
- REST API: productioon_plan.py
- Computes load matching with powerplants capabilities
by checking load between pmax and pmin
- Runtime error handling and logging to 'productionplan.log'


Requirements: Python 3.8+

Install by using toml by runing
`pip install .`
in the root folder of this repository.

Dependencies are described in pyproject.toml

POST payload
    curl -X POST -H "Content-Type: application/json" http://localhost:8888/productionplan

"""