from mcp.server.fastmcp import FastMCP

import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
import dateutil
import boto3

from botocore.exceptions import ClientError

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Crear el servidor MCP
mcp = FastMCP()

# Inicializar cliente SiteWise
try:
    sitewise = boto3.client('iotsitewise')
    logger.info("Cliente SiteWise inicializado correctamente")
except Exception as e:
    logger.error(f"Error inicializando cliente SiteWise: {str(e)}")
    sitewise = None



@mcp.tool()
def list_all_assets_hierarchy() -> Dict[str, Any]:
    """
    Obtiene TODOS los activos organizados en jerarquía: principales → hijos → nietos.
    
    Returns:
        Dict con todos los activos organizados por niveles jerárquicos
    """
    if not sitewise:
        raise Exception("Cliente SiteWise no disponible")
    
    try:
        all_assets = []
        
        # Obtener modelos
        models_response = sitewise.list_asset_models(maxResults=50)
        models = models_response.get('assetModelSummaries', [])
        
        for model in models:
            try:
                # Obtener todos los activos de este modelo
                assets_response = sitewise.list_assets(
                    assetModelId=model['id'],
                    maxResults=100
                )
                
                for asset in assets_response.get('assetSummaries', []):
                    asset_info = {
                        "id": asset['id'],
                        "name": asset['name'],
                        "model_name": model['name'],
                        "parent_id": asset.get('parentAssetId'),
                        "level": 0,  # Se calculará después
                        "arn": asset.get('arn', ''),
                        "creation_date": asset.get('creationDate', ''),
                        "last_update": asset.get('lastUpdateDate', '')
                    }
                    all_assets.append(asset_info)
                    
            except Exception as e:
                logger.warning(f"Error con modelo {model['id']}: {e}")
                continue
        
        # Organizar por jerarquía
        # 1. Identificar activos principales (sin padre)
        main_assets = [a for a in all_assets if not a['parent_id']]
        
        # 2. Crear diccionario para búsqueda rápida
        assets_dict = {a['id']: a for a in all_assets}
        
        # 3. Calcular niveles y organizar hijos
        def calculate_levels(asset_list, level=0):
            for asset in asset_list:
                asset['level'] = level
                # Encontrar hijos directos
                children = [a for a in all_assets if a['parent_id'] == asset['id']]
                asset['children'] = children
                asset['children_count'] = len(children)
                
                # Calcular niveles de hijos recursivamente
                if children:
                    calculate_levels(children, level + 1)
        
        # Aplicar cálculo de niveles
        calculate_levels(main_assets)
        
        # Crear lista plana ordenada por jerarquía
        def flatten_with_hierarchy(assets_list, flat_list):
            for asset in assets_list:
                # Añadir activo actual
                flat_list.append({
                    "id": asset['id'],
                    "name": asset['name'],
                    "model_name": asset['model_name'],
                    "level": asset['level'],
                    "parent_id": asset['parent_id'],
                    "children_count": asset['children_count'],
                    "arn": asset['arn'],
                    "creation_date": asset['creation_date'],
                    "last_update": asset['last_update']
                })
                
                # Añadir hijos recursivamente
                if asset.get('children'):
                    flatten_with_hierarchy(asset['children'], flat_list)
        
        # Generar lista final
        hierarchical_list = []
        flatten_with_hierarchy(main_assets, hierarchical_list)
        
        return {
            "success": True,
            "assets": hierarchical_list,
            "total_count": len(hierarchical_list),
            "main_assets_count": len(main_assets),
            "max_level": max([a['level'] for a in hierarchical_list]) if hierarchical_list else 0,
            "message": f"Jerarquía completa: {len(hierarchical_list)} activos organizados en {max([a['level'] for a in hierarchical_list]) + 1 if hierarchical_list else 0} niveles"
        }
        
    except Exception as e:
        raise Exception(f"Error obteniendo jerarquía completa: {str(e)}")


@mcp.tool()
def get_asset_properties(asset_id: str) -> Dict[str, Any]:
    """
    Obtiene todas las propiedades medibles de un activo específico.
    
    Args:
        asset_id: ID del activo
    
    Returns:
        Dict con las propiedades del activo
    """
    if not sitewise:
        raise Exception("Cliente SiteWise no disponible")
    
    try:
        response = sitewise.describe_asset(assetId=asset_id)
        properties = response.get('assetProperties', [])
        
        formatted_properties = []
        for prop in properties:
            prop_info = {
                "id": prop.get('id'),
                "name": prop.get('name'),
                "alias": prop.get('alias'),
                "dataType": prop.get('dataType'),
                "unit": prop.get('unit'),
                "dataTypeSpec": prop.get('dataTypeSpec'),
                "notification": prop.get('notification', {})
            }
            formatted_properties.append(prop_info)
        
        return {
            "success": True,
            "asset_id": asset_id,
            "asset_name": response.get('assetName'),
            "properties": formatted_properties,
            "count": len(formatted_properties)
        }
        
    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceNotFoundException':
            raise Exception(f"Activo {asset_id} no encontrado")
        raise Exception(f"Error AWS: {e.response['Error']['Message']}")
    except Exception as e:
        raise Exception(f"Error obteniendo propiedades: {str(e)}")


@mcp.tool()
def get_current_value(
    property_alias: Optional[str] = None, 
    asset_id: Optional[str] = None, 
    property_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Obtiene el valor actual de una propiedad específica.
    
    Args:
        property_alias: Alias de la propiedad (ej: /factory/motor1/temperature)
        asset_id: ID del activo (alternativa al alias)
        property_id: ID de la propiedad (usado con asset_id)
    
    Returns:
        Dict con el valor actual de la propiedad
    """
    if not sitewise:
        raise Exception("Cliente SiteWise no disponible")
    
    try:
        params = {}
        
        if property_alias:
            params['propertyAlias'] = property_alias
        elif asset_id and property_id:
            params['assetId'] = asset_id
            params['propertyId'] = property_id
        else:
            raise Exception("Debe proporcionar property_alias O (asset_id + property_id)")
        
        response = sitewise.get_asset_property_value(**params)
        property_value = response.get('propertyValue', {})
        
        return {
            "success": True,
            "property_alias": property_alias,
            "asset_id": asset_id,
            "property_id": property_id,
            "value": property_value.get('value'),
            "timestamp": property_value.get('timestamp'),
            "quality": property_value.get('quality'),
            "retrieved_at": datetime.now().isoformat()
        }
        
    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceNotFoundException':
            raise Exception("Propiedad no encontrada o sin datos")
        raise Exception(f"Error AWS: {e.response['Error']['Message']}")
    except Exception as e:
        raise Exception(f"Error obteniendo valor actual: {str(e)}")


@mcp.tool()
def get_historical_data(
    start_date: str, 
    end_date: str,
    property_alias: Optional[str] = None, 
    asset_id: Optional[str] = None, 
    property_id: Optional[str] = None,
    max_results: int = 100
) -> Dict[str, Any]:
    """
    Obtiene valores históricos de una propiedad en un rango de tiempo.
    
    Args:
        start_date: Fecha de inicio (ISO 8601: 2024-01-01T00:00:00Z)
        end_date: Fecha de fin (ISO 8601: 2024-01-02T00:00:00Z)
        property_alias: Alias de la propiedad
        asset_id: ID del activo (alternativa al alias)
        property_id: ID de la propiedad (usado con asset_id)
        max_results: Máximo número de valores (default: 100)
    
    Returns:
        Dict con los valores históricos de la propiedad
    """
    if not sitewise:
        raise Exception("Cliente SiteWise no disponible")
    
    try:
        # Convertir fechas ISO a timestamp Unix (segundos)
        start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
        end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
        
        params = {
            'startDate': int(start_dt.timestamp()),
            'endDate': int(end_dt.timestamp()),
            'maxResults': max_results,
            'timeOrdering': 'ASCENDING'
        }
        
        if property_alias:
            params['propertyAlias'] = property_alias
        elif asset_id and property_id:
            params['assetId'] = asset_id
            params['propertyId'] = property_id
        else:
            raise Exception("Debe proporcionar property_alias O (asset_id + property_id)")
        
        response = sitewise.get_asset_property_value_history(**params)
        values = response.get('assetPropertyValueHistory', [])
        
        formatted_values = []
        for value in values:
            formatted_values.append({
                "value": value.get('value'),
                "timestamp": value.get('timestamp'),
                "quality": value.get('quality')
            })
        
        return {
            "success": True,
            "property_alias": property_alias,
            "asset_id": asset_id,
            "property_id": property_id,
            "start_date": start_date,
            "end_date": end_date,
            "values": formatted_values,
            "count": len(formatted_values),
            "nextToken": response.get('nextToken'),
            "hasMore": 'nextToken' in response
        }
        
    except ValueError as e:
        raise Exception(f"Formato de fecha inválido: {str(e)}")
    except ClientError as e:
        raise Exception(f"Error AWS: {e.response['Error']['Message']}")
    except Exception as e:
        raise Exception(f"Error obteniendo historial: {str(e)}")


@mcp.tool()
def get_latest_values(
    property_alias: Optional[str] = None, 
    asset_id: Optional[str] = None, 
    property_id: Optional[str] = None,
    count: int = 10
) -> Dict[str, Any]:
    """
    Obtiene los últimos N valores de una propiedad.
    
    Args:
        property_alias: Alias de la propiedad
        asset_id: ID del activo (alternativa al alias)
        property_id: ID de la propiedad (usado con asset_id)
        count: Número de valores más recientes (default: 10)
    
    Returns:
        Dict con los últimos valores de la propiedad
    """
    if not sitewise:
        raise Exception("Cliente SiteWise no disponible")
    
    try:
        # Usar últimas 24 horas por defecto (timestamps Unix)
        end_date = datetime.now()
        start_date = end_date - timedelta(days=1)
        
        params = {
            'startDate': int(start_date.timestamp()),
            'endDate': int(end_date.timestamp()),
            'maxResults': count,
            'timeOrdering': 'DESCENDING'  # Más recientes primero
        }
        
        if property_alias:
            params['propertyAlias'] = property_alias
        elif asset_id and property_id:
            params['assetId'] = asset_id
            params['propertyId'] = property_id
        else:
            raise Exception("Debe proporcionar property_alias O (asset_id + property_id)")
        
        response = sitewise.get_asset_property_value_history(**params)
        values = response.get('assetPropertyValueHistory', [])
        
        formatted_values = []
        for value in values:
            formatted_values.append({
                "value": value.get('value'),
                "timestamp": value.get('timestamp'),
                "quality": value.get('quality')
            })
        
        return {
            "success": True,
            "property_alias": property_alias,
            "asset_id": asset_id,
            "property_id": property_id,
            "requested_count": count,
            "count": len(formatted_values),
            "actual_count": len(formatted_values),
            "values": formatted_values,
            "time_range": f"{start_date.isoformat()} to {end_date.isoformat()}"
        }
        
    except ClientError as e:
        raise Exception(f"Error AWS: {e.response['Error']['Message']}")
    except Exception as e:
        raise Exception(f"Error obteniendo últimos valores: {str(e)}")



if __name__ == '__main__':
    mcp.run()