#!/usr/bin/env python3

import sys
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
import boto3
import exceptiongroup
from botocore.exceptions import ClientError, NoCredentialsError
from mcp.server.fastmcp import FastMCP

# CRÍTICO: Configurar logging solo a stderr para MCP
logging.basicConfig(
    level=getattr(logging, os.getenv('LOG_LEVEL', 'INFO')),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stderr,  # SOLO stderr
    force=True
)
logger = logging.getLogger(__name__)

# Asegurar que todos los prints vayan a stderr
def debug_print(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)

# Crear el servidor MCP
mcp = FastMCP("sitewise-mcp-server")

# Función para configurar credenciales AWS
def configure_aws_credentials():
    """Configura las credenciales de AWS desde variables de entorno"""
    aws_config = {}
    
    # Región
    region = os.getenv('AWS_REGION', os.getenv('AWS_DEFAULT_REGION', 'us-east-1'))
    aws_config['region_name'] = region
    
    # Credenciales desde .env
    access_key = os.getenv('AWS_ACCESS_KEY_ID')
    secret_key = os.getenv('AWS_SECRET_ACCESS_KEY')
    session_token = os.getenv('AWS_SESSION_TOKEN')  # Para roles temporales
    
    if access_key and secret_key:
        aws_config['aws_access_key_id'] = access_key
        aws_config['aws_secret_access_key'] = secret_key
        logger.info("Usando credenciales AWS desde variables de entorno")
        
        if session_token:
            aws_config['aws_session_token'] = session_token
            logger.info("Incluyendo session token")
    else:
        logger.info("Usando credenciales AWS por defecto (profile, IAM role, etc.)")
    
    return aws_config

# Inicializar cliente SiteWise
sitewise = None
try:
    # Configurar credenciales
    aws_config = configure_aws_credentials()
    
    # Verificar credenciales con STS
    sts = boto3.client('sts', **aws_config)
    identity = sts.get_caller_identity()
    logger.info(f"AWS Identity: {identity.get('Arn', 'Unknown')}")
    logger.info(f"Account: {identity.get('Account', 'Unknown')}")
    logger.info(f"Region: {aws_config.get('region_name', 'default')}")
    
    # Crear cliente SiteWise
    sitewise = boto3.client('iotsitewise', **aws_config)
    logger.info("Cliente SiteWise inicializado correctamente")
    
except NoCredentialsError:
    logger.error("❌ Credenciales AWS no configuradas")
    logger.error("💡 Configura AWS_ACCESS_KEY_ID y AWS_SECRET_ACCESS_KEY en .env")
except ClientError as e:
    logger.error(f"❌ Error AWS: {e.response['Error']['Message']}")
    logger.error("💡 Verifica que las credenciales sean válidas y tengan permisos para SiteWise")
except Exception as e:
    logger.error(f"❌ Error inicializando SiteWise: {str(e)}")

@mcp.tool()
def health_check() -> Dict[str, Any]:
    """
    Verifica el estado del servidor y la conexión a AWS SiteWise.
    
    Returns:
        Dict con el estado del servidor y servicios
    """
    result = {
        "server": "sitewise-mcp-server",
        "version": "1.0.5",
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "services": {},
        "aws_config": {
            "region": os.getenv('AWS_REGION', 'us-east-1'),
            "using_env_credentials": bool(os.getenv('AWS_ACCESS_KEY_ID')),
            "profile": os.getenv('AWS_PROFILE', 'default')
        }
    }
    
    if sitewise:
        try:
            # Test simple con SiteWise
            response = sitewise.list_asset_models(maxResults=1)
            result["services"]["sitewise"] = {
                "status": "connected",
                "region": boto3.Session().region_name or os.getenv('AWS_REGION', 'default'),
                "test_response": "✅ Conexión exitosa"
            }
        except Exception as e:
            result["services"]["sitewise"] = {
                "status": "error",
                "error": str(e),
                "suggestion": "Verifica permisos IoT SiteWise en tu cuenta AWS"
            }
    else:
        result["services"]["sitewise"] = {
            "status": "not_initialized",
            "error": "Cliente no disponible",
            "suggestion": "Configura AWS_ACCESS_KEY_ID y AWS_SECRET_ACCESS_KEY en .env"
        }
    
    return result

@mcp.tool()
def list_all_assets_hierarchy() -> Dict[str, Any]:
    """
    Obtiene todos los activos organizados en jerarquía: principales → hijos → nietos.
    
    Returns:
        Dict con todos los activos organizados por niveles jerárquicos
    """
    if not sitewise:
        return {
            "success": False,
            "error": "Cliente SiteWise no disponible. Configurar credenciales AWS en .env"
        }
    
    try:
        all_assets = []
        models_count = 0
        
        # Obtener modelos con paginación
        paginator = sitewise.get_paginator('list_asset_models')
        for page in paginator.paginate(maxResults=50):
            models = page.get('assetModelSummaries', [])
            models_count += len(models)
            
            for model in models:
                try:
                    asset_paginator = sitewise.get_paginator('list_assets')
                    for asset_page in asset_paginator.paginate(
                        assetModelId=model['id'],
                        maxResults=100
                    ):
                        for asset in asset_page.get('assetSummaries', []):
                            asset_info = {
                                "id": asset['id'],
                                "name": asset['name'],
                                "model_name": model['name'],
                                "model_id": model['id'],
                                "parent_id": asset.get('parentAssetId'),
                                "level": 0,
                                "arn": asset.get('arn', ''),
                                "creation_date": asset.get('creationDate', '').isoformat() if asset.get('creationDate') else '',
                                "last_update": asset.get('lastUpdateDate', '').isoformat() if asset.get('lastUpdateDate') else ''
                            }
                            all_assets.append(asset_info)
                            
                except Exception as e:
                    logger.warning(f"Error con modelo {model['id']}: {e}")
                    continue
        
        if not all_assets:
            return {
                "success": True,
                "assets": [],
                "total_count": 0,
                "message": f"No se encontraron activos en {models_count} modelos"
            }
        
        # Organizar por jerarquía
        main_assets = [a for a in all_assets if not a['parent_id']]
        
        def calculate_levels(asset_list, level=0):
            for asset in asset_list:
                asset['level'] = level
                children = [a for a in all_assets if a['parent_id'] == asset['id']]
                asset['children'] = children
                asset['children_count'] = len(children)
                
                if children:
                    calculate_levels(children, level + 1)
        
        calculate_levels(main_assets)
        
        # Crear lista plana ordenada por jerarquía
        def flatten_with_hierarchy(assets_list, flat_list):
            for asset in assets_list:
                flat_list.append({
                    "id": asset['id'],
                    "name": asset['name'],
                    "model_name": asset['model_name'],
                    "model_id": asset['model_id'],
                    "level": asset['level'],
                    "parent_id": asset['parent_id'],
                    "children_count": asset['children_count'],
                    "arn": asset['arn'],
                    "creation_date": asset['creation_date'],
                    "last_update": asset['last_update']
                })
                
                if asset.get('children'):
                    flatten_with_hierarchy(asset['children'], flat_list)
        
        hierarchical_list = []
        flatten_with_hierarchy(main_assets, hierarchical_list)
        
        return {
            "success": True,
            "assets": hierarchical_list,
            "total_count": len(hierarchical_list),
            "main_assets_count": len(main_assets),
            "models_count": models_count,
            "max_level": max([a['level'] for a in hierarchical_list]) if hierarchical_list else 0,
            "message": f"Jerarquía completa: {len(hierarchical_list)} activos en {max([a['level'] for a in hierarchical_list]) + 1 if hierarchical_list else 0} niveles"
        }
        
    except ClientError as e:
        return {
            "success": False,
            "error": f"Error AWS: {e.response['Error']['Message']}"
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Error obteniendo jerarquía: {str(e)}"
        }

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
        return {
            "success": False,
            "error": "Cliente SiteWise no disponible"
        }
    
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
            "asset_model_id": response.get('assetModelId'),
            "properties": formatted_properties,
            "count": len(formatted_properties)
        }
        
    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceNotFoundException':
            return {
                "success": False,
                "error": f"Activo {asset_id} no encontrado"
            }
        return {
            "success": False,
            "error": f"Error AWS: {e.response['Error']['Message']}"
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Error obteniendo propiedades: {str(e)}"
        }

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
        return {
            "success": False,
            "error": "Cliente SiteWise no disponible"
        }
    
    try:
        params = {}
        
        if property_alias:
            params['propertyAlias'] = property_alias
        elif asset_id and property_id:
            params['assetId'] = asset_id
            params['propertyId'] = property_id
        else:
            return {
                "success": False,
                "error": "Debe proporcionar property_alias O (asset_id + property_id)"
            }
        
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
            return {
                "success": False,
                "error": "Propiedad no encontrada o sin datos"
            }
        return {
            "success": False,
            "error": f"Error AWS: {e.response['Error']['Message']}"
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Error obteniendo valor actual: {str(e)}"
        }

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
        return {
            "success": False,
            "error": "Cliente SiteWise no disponible"
        }
    
    try:
        start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
        end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
        
        params = {
            'startDate': int(start_dt.timestamp()),
            'endDate': int(end_dt.timestamp()),
            'maxResults': min(max_results, 20000),
            'timeOrdering': 'ASCENDING'
        }
        
        if property_alias:
            params['propertyAlias'] = property_alias
        elif asset_id and property_id:
            params['assetId'] = asset_id
            params['propertyId'] = property_id
        else:
            return {
                "success": False,
                "error": "Debe proporcionar property_alias O (asset_id + property_id)"
            }
        
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
        return {
            "success": False,
            "error": f"Formato de fecha inválido: {str(e)}"
        }
    except ClientError as e:
        return {
            "success": False,
            "error": f"Error AWS: {e.response['Error']['Message']}"
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Error obteniendo historial: {str(e)}"
        }

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
        return {
            "success": False,
            "error": "Cliente SiteWise no disponible"
        }
    
    try:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=1)
        
        params = {
            'startDate': int(start_date.timestamp()),
            'endDate': int(end_date.timestamp()),
            'maxResults': min(count, 20000),
            'timeOrdering': 'DESCENDING'
        }
        
        if property_alias:
            params['propertyAlias'] = property_alias
        elif asset_id and property_id:
            params['assetId'] = asset_id
            params['propertyId'] = property_id
        else:
            return {
                "success": False,
                "error": "Debe proporcionar property_alias O (asset_id + property_id)"
            }
        
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
            "actual_count": len(formatted_values),
            "values": formatted_values,
            "time_range": f"{start_date.isoformat()} to {end_date.isoformat()}"
        }
        
    except ClientError as e:
        return {
            "success": False,
            "error": f"Error AWS: {e.response['Error']['Message']}"
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Error obteniendo últimos valores: {str(e)}"
        }

# Función principal sin logs a stdout
if __name__ == '__main__':
    try:
        logger.info("🚀 Iniciando servidor MCP SiteWise")
        if sitewise:
            logger.info("✅ Servidor listo para conexiones MCP")
        else:
            logger.warning("⚠️  Servidor sin conexión SiteWise - verificar credenciales")
        
        # Solo ejecutar MCP, sin otros prints
        mcp.run()
    except KeyboardInterrupt:
        logger.info("🛑 Servidor detenido por usuario")
    except Exception as e:
        logger.error(f"❌ Error ejecutando servidor: {e}")
        sys.exit(1)