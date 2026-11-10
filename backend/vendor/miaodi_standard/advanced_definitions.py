"""Compile proven advanced-feature definitions without exporting private rules.

Caller supplies bounded, readonly definition snapshots. Content hashes and every
stored definition must agree before a definition can be consumed. A declared
missing-is-zero policy never authorizes this layer to invent a measured zero.
"""
import hashlib
import json
from typing import Literal
from .api_contract import DTO,Id,Code
from .codec import digest
from .contract import number,stable_id

FEATURE_COLUMNS=('feature_name','scope','name_en','name_zh','unit','aggregation_method',
                 'zero_policy','proxy_status','analysis_eligible','source_basis','expression','limitations')
UNITS={'boolean':'boolean','category':'category','count':'count','0-1 share':'share',
       'matches':'count','minutes':'minutes','days':'days','ratio':'ratio','mixed_count_and_xg':'composite'}
# These reviewed source contracts define component sets despite a count unit.
# They are not a single scalar and must never be summed under the parent name.
COMPONENT_SETS={('opta_progression_v1','five_channel_progression'),
                ('opta_transition_pressing_v1','official_defensive_actions'),
                ('opta_transition_pressing_v1','opponent_shots_after_loss_proxy')}


class FeatureDefinition(DTO):
    schema_version: Literal['miaodi-advanced-feature-definition.v1']
    definition_id: Id
    series_id: Id
    metric_code: Code
    entity_scope: Literal['event','team_match','player_match','channel_match']
    name: str
    name_en: str
    unit: Literal['boolean','category','count','share','minutes','days','ratio','composite']
    provenance_kind: Literal['provider','project_derived','proxy','unavailable']
    analysis_eligible: bool
    representation: Literal['scalar','components','unavailable']
    zero_policy: Literal['may_include_source_defaults','preserve_missing']


class FeatureObservation(DTO):
    schema_version: Literal['miaodi-advanced-feature-observation.v1'] = 'miaodi-advanced-feature-observation.v1'
    definition_id: Id
    series_id: Id
    metric_code: Code
    value: int | float | bool | str | None
    status: Literal['missing','available','unavailable','invalid']
    issues: list[Literal['composite_requires_typed_components','feature_not_analysis_eligible',
                         'zero_may_include_source_defaults','invalid_feature_value']]


def compile_catalog(contract_bytes,stored_contracts,stored_features):
    headers={}
    for row in stored_contracts:
        version=row['definition_version']
        if version in headers:raise ValueError('duplicate_feature_contract')
        headers[version]=row
    expected={};result=[]
    if set(contract_bytes)!=set(headers):raise ValueError('feature_contract_set_mismatch')
    for version,body in sorted(contract_bytes.items()):
        header=headers[version];contract=json.loads(body)
        if (hashlib.sha256(body).hexdigest()!=header['content_sha256'] or
            contract.get('definition_version')!=version or contract.get('contract_id')!=header['contract_id'] or
            contract.get('status')!='active' or header['status']!='active'):
            raise ValueError('feature_contract_evidence_mismatch')
        for feature in contract['features']:
            key=(version,feature['scope'],feature['feature_name'])
            if key in expected:raise ValueError('duplicate_feature_definition')
            expected[key]={column:int(bool(feature[column])) if column=='analysis_eligible' else str(feature[column])
                           for column in FEATURE_COLUMNS}
    seen=set()
    for row in stored_features:
        key=(row['definition_version'],row['scope'],row['feature_name'])
        if key in seen:raise ValueError('duplicate_stored_feature')
        seen.add(key)
        if key not in expected or any(row[column]!=value for column,value in expected[key].items()):
            raise ValueError('feature_definition_evidence_mismatch')
        if row['unit'] not in UNITS:raise ValueError('unmapped_feature_unit')
        if row['zero_policy'] not in ('missing_is_zero','missing_is_null'):
            raise ValueError('unmapped_feature_zero_policy')
        unit=UNITS[row['unit']]
        representation=('unavailable' if not row['analysis_eligible'] or row['proxy_status']=='unavailable'
                        else 'components' if unit=='composite' or (key[0],key[2]) in COMPONENT_SETS else 'scalar')
        identity=stable_id('feature-definition','miaodi',digest({'contract':headers[key[0]]['content_sha256'],
                           'own_schema':'miaodi-advanced-feature-definition.v1','unit':unit,'representation':representation,
                           'row':{'definition_version':key[0],**expected[key]}}))
        result.append(FeatureDefinition.model_validate({
            'schema_version':'miaodi-advanced-feature-definition.v1','definition_id':identity,
            'series_id':stable_id('series','miaodi',identity),'metric_code':row['feature_name'],
            'entity_scope':row['scope'],'name':row['name_zh'],'name_en':row['name_en'],'unit':unit,
            'provenance_kind':row['proxy_status'],
            'analysis_eligible':bool(row['analysis_eligible']) and row['proxy_status']!='unavailable',
            'representation':representation,
            'zero_policy':'may_include_source_defaults' if row['zero_policy']=='missing_is_zero' else 'preserve_missing',
        }).model_dump())
    if seen!=set(expected):raise ValueError('missing_stored_feature_definition')
    return sorted(result,key=lambda row:row['definition_id'])


def feature_observation(definition,value,*,present=True):
    definition=FeatureDefinition.model_validate(definition)
    if type(present) is not bool:raise ValueError('invalid_feature_presence')
    result={'definition_id':definition.definition_id,'series_id':definition.series_id,
            'metric_code':definition.metric_code,'value':None,'status':'missing','issues':[]}
    if not definition.analysis_eligible or definition.representation!='scalar':
        result['status']='unavailable'
        result['issues']=['composite_requires_typed_components' if definition.representation=='components' else 'feature_not_analysis_eligible']
        return FeatureObservation.model_validate(result).model_dump()
    if not present or value is None:return FeatureObservation.model_validate(result).model_dump()
    unit=definition.unit
    try:
        if unit=='boolean':
            if type(value) is not bool:raise ValueError('invalid_boolean_feature')
            converted=value
        elif unit=='category':
            if not isinstance(value,str) or not value or len(value)>128:raise ValueError('invalid_category_feature')
            converted=value
        else:
            converted=number(value,'count' if unit=='count' else 'number',0,1 if unit=='share' else None)
        result.update(value=converted,status='available')
        if converted==0 and definition.zero_policy=='may_include_source_defaults':
            result['issues'].append('zero_may_include_source_defaults')
    except ValueError:
        result['status']='invalid';result['issues']=['invalid_feature_value']
    return FeatureObservation.model_validate(result).model_dump()
