"""
The purpose of this file is to:
1. Interact with the Wikidata API
2. Store the results
3. Make the results easy to access from other files
"""

import requests
from dataclasses import dataclass
from openlibrary.core.helpers import days_since

from datetime import datetime
import json
from openlibrary.core import db

WIKIDATA_API_URL = 'https://www.wikidata.org/w/rest.php/wikibase/v0/entities/items/'
WIKIDATA_CACHE_TTL_DAYS = 30


@dataclass
class WikidataEntity:
    """
    This is the model of the api response from WikiData plus the updated field
    https://www.wikidata.org/wiki/Wikidata:REST_API
    """

    id: str
    type: str
    labels: dict[str, str]
    descriptions: dict[str, str]
    aliases: dict[str, list[str]]
    statements: dict[str, dict]
    sitelinks: dict[str, dict]
    updated: datetime  # This is when we fetched the data, not when the entity was changed in Wikidata

    def description(self, language: str = 'en') -> str | None:
        """If a description isn't available in the requested language default to English"""
        return self.descriptions.get(language) or self.descriptions.get('en')

    @classmethod
    def from_dict(cls, response: dict, updated: datetime):
        return cls(
            id=response['id'],
            type=response['type'],
            labels=response['labels'],
            descriptions=response['descriptions'],
            aliases=response['aliases'],
            statements=response['statements'],
            sitelinks=response['sitelinks'],
            updated=updated,
        )

    def as_api_response_str(self) -> str:
        """
        Transforms the dataclass a JSON string like we get from the Wikidata API.
        This is used for staring the json in the database.
        """
        entity_dict = {
            'id': self.id,
            'type': self.type,
            'labels': self.labels,
            'descriptions': self.descriptions,
            'aliases': self.aliases,
            'statements': self.statements,
            'sitelinks': self.sitelinks,
        }
        return json.dumps(entity_dict)


def _cache_expired(entity: WikidataEntity) -> bool:
    return days_since(entity.updated) > WIKIDATA_CACHE_TTL_DAYS


def get_wikidata_entity(
    qid: str, bust_cache: bool = False, fetch_missing: bool = False
) -> WikidataEntity | None:
    """
    This only supports QIDs, if we want to support PIDs we need to use different endpoints
    By default this will only use the cache (unless it is expired).
    This is to avoid overwhelming Wikidata servers with requests from every visit to an author page.
    bust_cache must be set to True if you want to fetch new items from Wikidata.
    """
    if bust_cache:
        _get_from_web(qid)

    if entity := _get_from_cache(qid):
        if _cache_expired(entity):
            return _get_from_web(qid)
        return entity

    if fetch_missing and not entity:
        return _get_from_web(qid)

    return None


def _get_from_web(id: str) -> WikidataEntity | None:
    response = requests.get(f'{WIKIDATA_API_URL}{id}')
    if response.status_code == 200:
        entity = WikidataEntity.from_dict(
            response=response.json(), updated=datetime.now()
        )
        _add_to_cache(entity)
        return entity
    else:
        return None
    # TODO: What should we do in non-200 cases?
    # They're documented here https://doc.wikimedia.org/Wikibase/master/js/rest-api/


def _get_from_cache_by_ids(ids: list[str]) -> list[WikidataEntity]:
    response = list(
        db.get_db().query(
            'select * from wikidata where id IN ($ids)',
            vars={'ids': ids},
        )
    )
    return [
        WikidataEntity.from_dict(response=r.data, updated=r.updated) for r in response
    ]


def _get_from_cache(id: str) -> WikidataEntity | None:
    """
    The cache is OpenLibrary's Postgres instead of calling the Wikidata API
    """
    if len(result := _get_from_cache_by_ids([id])) > 0:
        return result[0]
    return None


def _add_to_cache(entity: WikidataEntity) -> None:
    # TODO: after we upgrade to postgres 9.5+ we should use upsert here
    oldb = db.get_db()
    json_data = entity.as_api_response_str()

    if _get_from_cache(entity.id):
        return oldb.update(
            "wikidata",
            where="id=$id",
            vars={'id': entity.id},
            data=json_data,
            updated=entity.updated,
        )
    else:
        # We don't provide the updated column on insert because postgres defaults to the current time
        return oldb.insert("wikidata", id=entity.id, data=json_data)