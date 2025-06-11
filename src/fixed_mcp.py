import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List

import boto3
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from botocore.exceptions import ClientError

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Inicializar cliente SiteWise
try:
    sitewise = boto3.client('iotsitewise')
    logger.info("Cliente SiteWise inicializado correctamente")
except Exception as e:
    logger.error(f"Error inicializando cliente SiteWise: {str(e)}")
    sitewise = None

# Crear aplicación FastAPI
app = FastAPI(title="SiteWise Measurement MCP Server")

# Herramientas orientadas a medición
MEASUREMENT_TOOLS = [
    {
        "name": "list_assets",
        "description": "Lista activos disponibles con sus propiedades medibles",
        "inputSchema": {
            "type": "object",
            "properties": {
                "max_results": {
                    "type": "integer",
                    "description": "Máximo número de activos (default: 20)",
                    "default": 20
                }
            },
            "additionalProperties": False
        }
    },
    {
        "name": "get_asset_properties",
        "description": "Obtiene todas las propiedades medibles de un activo específico",
        "inputSchema": {
            "type": "object",
            "properties": {
                "asset_id": {
                    "type": "string",
                    "description": "ID del activo"
                }
            },
            "required": ["asset_id"],
            "additionalProperties": False
        }
    },
    {
        "name": "get_current_value",
        "description": "Obtiene el valor actual de una propiedad específica",
        "inputSchema": {
            "type": "object",
            "properties": {
                "property_alias": {
                    "type": "string",
                    "description": "Alias de la propiedad (ej: /factory/motor1/temperature)"
                },
                "asset_id": {
                    "type": "string",
                    "description": "ID del activo (alternativa al alias)"
                },
                "property_id": {
                    "type": "string",
                    "description": "ID de la propiedad (usado con asset_id)"
                }
            },
            "additionalProperties": False
        }
    },
    {
        "name": "get_historical_data",
        "description": "Obtiene valores históricos de una propiedad en un rango de tiempo",
        "inputSchema": {
            "type": "object",
            "properties": {
                "property_alias": {
                    "type": "string",
                    "description": "Alias de la propiedad"
                },
                "asset_id": {
                    "type": "string",
                    "description": "ID del activo (alternativa al alias)"
                },
                "property_id": {
                    "type": "string",
                    "description": "ID de la propiedad (usado con asset_id)"
                },
                "start_date": {
                    "type": "string",
                    "description": "Fecha de inicio (ISO 8601: 2024-01-01T00:00:00Z)"
                },
                "end_date": {
                    "type": "string",
                    "description": "Fecha de fin (ISO 8601: 2024-01-02T00:00:00Z)"
                },
                "max_results": {
                    "type": "integer",
                    "description": "Máximo número de valores (default: 100)",
                    "default": 100
                }
            },
            "required": ["start_date", "end_date"],
            "additionalProperties": False
        }
    },
    {
        "name": "get_latest_values",
        "description": "Obtiene los últimos N valores de una propiedad",
        "inputSchema": {
            "type": "object",
            "properties": {
                "property_alias": {
                    "type": "string",
                    "description": "Alias de la propiedad"
                },
                "asset_id": {
                    "type": "string",
                    "description": "ID del activo (alternativa al alias)"
                },
                "property_id": {
                    "type": "string",
                    "description": "ID de la propiedad (usado con asset_id)"
                },
                "count": {
                    "type": "integer",
                    "description": "Número de valores más recientes (default: 10)",
                    "default": 10
                }
            },
            "additionalProperties": False
        }
    }
]

# Implementación de herramientas de medición
def execute_list_assets(max_results: int = 20) -> Dict[str, Any]:
    """Lista activos con sus propiedades medibles"""
    if not sitewise:
        raise Exception("Cliente SiteWise no disponible")
    
    try:
        # Obtener primeros modelos
        models_response = sitewise.list_asset_models(maxResults=10)
        models = models_response.get('assetModelSummaries', [])
        
        all_assets = []
        
        for model in models[:5]:  # Limitar a 5 modelos para evitar timeouts
            try:
                assets_response = sitewise.list_assets(
                    assetModelId=model['id'],
                    maxResults=max(1, max_results // len(models[:5]))
                )
                
                for asset in assets_response.get('assetSummaries', []):
                    # Obtener propiedades del activo
                    try:
                        asset_details = sitewise.describe_asset(assetId=asset['id'])
                        properties = asset_details.get('assetProperties', [])
                        
                        # Solo incluir activos con propiedades
                        if properties:
                            asset_info = {
                                "id": asset['id'],
                                "name": asset['name'],
                                "model_name": model['name'],
                                "properties_count": len(properties),
                                "properties": [
                                    {
                                        "id": prop.get('id'),
                                        "name": prop.get('name'),
                                        "alias": prop.get('alias'),
                                        "dataType": prop.get('dataType'),
                                        "unit": prop.get('unit')
                                    }
                                    for prop in properties[:5]  # Primeras 5 propiedades
                                ]
                            }
                            all_assets.append(asset_info)
                            
                            # Limitar resultados para evitar timeouts
                            if len(all_assets) >= max_results:
                                break
                                
                    except Exception as e:
                        logger.warning(f"Error obteniendo detalles del asset {asset['id']}: {e}")
                        continue
                
                # Break outer loop si ya tenemos suficientes activos
                if len(all_assets) >= max_results:
                    break
                        
            except Exception as e:
                logger.warning(f"Error listando assets del modelo {model['id']}: {e}")
                continue
        
        return {
            "success": True,
            "assets": all_assets[:max_results],
            "count": len(all_assets[:max_results]),
            "message": f"Encontrados {len(all_assets)} activos con propiedades medibles"
        }
        
    except Exception as e:
        raise Exception(f"Error listando activos: {str(e)}")

def execute_get_asset_properties(asset_id: str) -> Dict[str, Any]:
    """Obtiene todas las propiedades medibles de un activo"""
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

def execute_get_current_value(property_alias: str = None, asset_id: str = None, property_id: str = None) -> Dict[str, Any]:
    """Obtiene valor actual de una propiedad"""
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

def execute_get_historical_data(
    start_date: str, 
    end_date: str,
    property_alias: str = None, 
    asset_id: str = None, 
    property_id: str = None,
    max_results: int = 100
) -> Dict[str, Any]:
    """Obtiene datos históricos de una propiedad"""
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

def execute_get_latest_values(
    property_alias: str = None, 
    asset_id: str = None, 
    property_id: str = None,
    count: int = 10
) -> Dict[str, Any]:
    """Obtiene los últimos N valores de una propiedad"""
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
            "count": len(formatted_values),  # Asegurar que count esté presente
            "actual_count": len(formatted_values),
            "values": formatted_values,
            "time_range": f"{start_date.isoformat()} to {end_date.isoformat()}"
        }
        
    except ClientError as e:
        raise Exception(f"Error AWS: {e.response['Error']['Message']}")
    except Exception as e:
        raise Exception(f"Error obteniendo últimos valores: {str(e)}")

# Endpoints FastAPI
@app.get("/")
def root():
    """Endpoint raíz con información del servidor"""
    return JSONResponse({
        "message": "SiteWise Measurement MCP Server",
        "protocol": "Model Context Protocol",
        "version": "1.0.0",
        "purpose": "Industrial Data Measurement & Monitoring",
        "endpoints": {
            "mcp": "POST /",
            "health": "GET /health"
        },
        "tools_available": len(MEASUREMENT_TOOLS),
        "timestamp": datetime.now().isoformat()
    })

@app.get("/health")
def health_check():
    """Health check endpoint"""
    sitewise_status = "available" if sitewise else "unavailable"
    return JSONResponse({
        "status": "healthy",
        "sitewise": sitewise_status,
        "timestamp": datetime.now().isoformat(),
        "server": "sitewise-measurement-mcp-server",
        "version": "1.0.0",
        "purpose": "measurement"
    })

@app.post("/")
async def mcp_handler(request: Request):
    """Handler principal para protocolo MCP"""
    try:
        body = await request.json()
        
        # Validar estructura básica
        if not isinstance(body, dict):
            return create_error_response(1, -32600, "Invalid Request")
        
        jsonrpc = body.get('jsonrpc')
        method = body.get('method')
        request_id = body.get('id', 1)
        params = body.get('params', {})
        
        if jsonrpc != "2.0":
            return create_error_response(request_id, -32600, "Invalid JSON-RPC version")
        
        if not method:
            return create_error_response(request_id, -32600, "Missing method")
        
        # Router de métodos MCP
        if method == "initialize":
            return handle_initialize(request_id, params)
        elif method == "tools/list":
            return handle_list_tools(request_id, params)
        elif method == "tools/call":
            return handle_tool_call(request_id, params)
        else:
            return create_error_response(request_id, -32601, f"Method not found: {method}")
            
    except json.JSONDecodeError:
        return create_error_response(1, -32700, "Parse error")
    except Exception as e:
        logger.error(f"Error en MCP handler: {str(e)}")
        return create_error_response(1, -32603, f"Internal error: {str(e)}")

def create_error_response(request_id: int, code: int, message: str) -> Dict[str, Any]:
    """Crear respuesta de error estándar"""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": code,
            "message": message
        }
    }

def create_success_response(request_id: int, result: Dict[str, Any]) -> Dict[str, Any]:
    """Crear respuesta exitosa estándar"""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result
    }

def handle_initialize(request_id: int, params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle initialize request"""
    return create_success_response(request_id, {
        "protocolVersion": "2024-11-05",
        "capabilities": {
            "tools": {},
            "resources": {}
        },
        "serverInfo": {
            "name": "sitewise-measurement-mcp-server",
            "version": "1.0.0"
        }
    })

def handle_list_tools(request_id: int, params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle list tools request"""
    return create_success_response(request_id, {"tools": MEASUREMENT_TOOLS})

def handle_tool_call(request_id: int, params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle tool call request"""
    try:
        tool_name = params.get('name')
        # Manejar argumentos que pueden ser None
        arguments = params.get('arguments')
        if arguments is None:
            arguments = {}
        
        if not tool_name:
            return create_error_response(request_id, -32602, "Missing tool name")
        
        # Ejecutar herramienta
        try:
            if tool_name == 'list_assets':
                result = execute_list_assets(
                    max_results=arguments.get('max_results', 20)
                )
            elif tool_name == 'get_asset_properties':
                asset_id = arguments.get('asset_id')
                if not asset_id:
                    return create_error_response(request_id, -32602, "Missing required parameter: asset_id")
                result = execute_get_asset_properties(asset_id)
            elif tool_name == 'get_current_value':
                result = execute_get_current_value(
                    property_alias=arguments.get('property_alias'),
                    asset_id=arguments.get('asset_id'),
                    property_id=arguments.get('property_id')
                )
            elif tool_name == 'get_historical_data':
                start_date = arguments.get('start_date')
                end_date = arguments.get('end_date')
                if not start_date or not end_date:
                    return create_error_response(request_id, -32602, "Missing required parameters: start_date and end_date")
                result = execute_get_historical_data(
                    start_date=start_date,
                    end_date=end_date,
                    property_alias=arguments.get('property_alias'),
                    asset_id=arguments.get('asset_id'),
                    property_id=arguments.get('property_id'),
                    max_results=arguments.get('max_results', 100)
                )
            elif tool_name == 'get_latest_values':
                result = execute_get_latest_values(
                    property_alias=arguments.get('property_alias'),
                    asset_id=arguments.get('asset_id'),
                    property_id=arguments.get('property_id'),
                    count=arguments.get('count', 10)
                )
            else:
                return create_error_response(request_id, -32601, f"Unknown tool: {tool_name}")
            
            return create_success_response(request_id, {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(result, indent=2, default=str)
                    }
                ]
            })
            
        except Exception as e:
            logger.error(f"Error ejecutando herramienta {tool_name}: {str(e)}")
            return create_error_response(request_id, -32603, f"Tool execution error: {str(e)}")
            
    except Exception as e:
        logger.error(f"Error en handle_tool_call: {str(e)}")
        return create_error_response(request_id, -32603, f"Handler error: {str(e)}")

# Ejecutar servidor
if __name__ == "__main__":
    import uvicorn
    
    print("🚀 Iniciando SiteWise Measurement MCP Server")
    print("📊 Enfoque: Datos de medición industrial en tiempo real")
    print("🔧 MCP endpoint: http://localhost:8000")
    print("📋 Health check: http://localhost:8000/health")
    print("🏭 Herramientas de medición:")
    print("   • list_assets - Lista activos con propiedades medibles")
    print("   • get_asset_properties - Propiedades de un activo")
    print("   • get_current_value - Valor actual de una medición")
    print("   • get_historical_data - Historial en rango de tiempo")
    print("   • get_latest_values - Últimos N valores")
    
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")