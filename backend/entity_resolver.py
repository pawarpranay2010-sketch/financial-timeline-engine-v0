"""
=========================================================
Module 3.8 - Entity Resolver
=========================================================

Purpose:
Identify important entities inside financial documents
and map relationships between them.

Examples

HDFC Bank
    ↓
Parent of
HDB Financial Services

SEBI
    ↓
Regulator

RBI
    ↓
Banking Regulator

This module DOES NOT fetch external data.
It only resolves relationships from detected entities.

Author:
Finance Intelligence Engine
"""

import re
from copy import deepcopy


# -------------------------------------------------------
# Knowledge Base
# -------------------------------------------------------

ENTITY_DATABASE = {

    "HDFC Bank": {

        "type": "Bank",

        "sector": "Banking",

        "regulator": "RBI",

        "children": [

            "HDB Financial Services"

        ]

    },

    "HDB Financial Services": {

        "type": "NBFC",

        "sector": "Financial Services",

        "parent": "HDFC Bank",

        "regulator": "RBI"

    },

    "ICICI Bank": {

        "type": "Bank",

        "sector": "Banking",

        "regulator": "RBI"

    },

    "Reliance Industries": {

        "type": "Conglomerate",

        "sector": "Diversified"

    },

    "Infosys": {

        "type": "Technology",

        "sector": "IT"

    }

}


# -------------------------------------------------------
# Detect Entities
# -------------------------------------------------------

def detect_entities(text):

    found = []

    if not isinstance(text, str):
        return found

    for entity in ENTITY_DATABASE:

        if re.search(
            re.escape(entity),
            text,
            re.IGNORECASE
        ):

            info = deepcopy(
                ENTITY_DATABASE[entity]
            )

            info["name"] = entity

            found.append(info)

    return found


# -------------------------------------------------------
# Relationship Builder
# -------------------------------------------------------

def build_relationships(entities):

    relationships = []

    for entity in entities:

        if "parent" in entity:

            relationships.append({

                "source":
                    entity["name"],

                "relation":
                    "Subsidiary Of",

                "target":
                    entity["parent"]

            })

        if "children" in entity:

            for child in entity["children"]:

                relationships.append({

                    "source":
                        entity["name"],

                    "relation":
                        "Parent Of",

                    "target":
                        child

                })

        if "regulator" in entity:

            relationships.append({

                "source":
                    entity["name"],

                "relation":
                    "Regulated By",

                "target":
                    entity["regulator"]

            })

    return relationships


# -------------------------------------------------------
# Recursive Search
# -------------------------------------------------------

def _collect_text(data):

    text = ""

    if isinstance(data, dict):

        for value in data.values():

            text += " " + _collect_text(value)

    elif isinstance(data, list):

        for item in data:

            text += " " + _collect_text(item)

    elif isinstance(data, str):

        text += " " + data

    return text


# -------------------------------------------------------
# Public API
# -------------------------------------------------------

def resolve_entities(module3_result):

    if not isinstance(module3_result, dict):
        raise TypeError(
            "Module3 output must be dictionary."
        )

    result = deepcopy(module3_result)

    full_text = _collect_text(result)

    entities = detect_entities(full_text)

    relationships = build_relationships(entities)

    result["entities"] = entities

    result["entity_relationships"] = relationships

    return result


# -------------------------------------------------------
# Local Testing
# -------------------------------------------------------

if __name__ == "__main__":

    sample = {

        "memo":

        """
        HDFC Bank announced that
        HDB Financial Services
        completed its IPO.
        RBI regulations remain
        unchanged.
        """

    }

    from pprint import pprint

    pprint(resolve_entities(sample))
