#!/usr/bin/env node

const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

// Colores para la consola (compatible con Windows)
const colors = {
  reset: '\x1b[0m',
  bright: '\x1b[1m',
  red: '\x1b[31m',
  green: '\x1b[32m',
  yellow: '\x1b[33m',
  blue: '\x1b[34m',
  magenta: '\x1b[35m',
  cyan: '\x1b[36m',
  white: '\x1b[37m'
};

// Detectar si los colores son soportados
const supportsColor = process.platform !== 'win32' || process.env.FORCE_COLOR || process.env.CI;

function log(message, color = 'reset') {
  if (supportsColor) {
    console.log(`${colors[color]}${message}${colors.reset}`);
  } else {
    console.log(message);
  }
}

function checkPython() {
  return new Promise((resolve, reject) => {
    const python = spawn('python3', ['--version']);
    
    python.on('close', (code) => {
      if (code === 0) {
        resolve('python3');
      } else {
        // Intenta con python
        const pythonAlt = spawn('python', ['--version']);
        pythonAlt.on('close', (altCode) => {
          if (altCode === 0) {
            resolve('python');
          } else {
            reject(new Error('Python no está instalado o no está en el PATH'));
          }
        });
      }
    });
    
    python.on('error', () => {
      // Intenta con python
      const pythonAlt = spawn('python', ['--version']);
      pythonAlt.on('close', (altCode) => {
        if (altCode === 0) {
          resolve('python');
        } else {
          reject(new Error('Python no está instalado o no está en el PATH'));
        }
      });
      pythonAlt.on('error', () => {
        reject(new Error('Python no está instalado o no está en el PATH'));
      });
    });
  });
}

function installDependencies(pythonCmd, srcDir) {
  return new Promise((resolve, reject) => {
    log('📦 Instalando dependencias de Python...', 'yellow');
    
    const requirementsPath = path.join(srcDir, 'requirements.txt');
    
    if (!fs.existsSync(requirementsPath)) {
      reject(new Error(`No se encontró requirements.txt en ${requirementsPath}`));
      return;
    }
    
    const pip = spawn(pythonCmd, ['-m', 'pip', 'install', '-r', requirementsPath], {
      stdio: 'inherit'
    });
    
    pip.on('close', (code) => {
      if (code === 0) {
        log('✅ Dependencias instaladas correctamente', 'green');
        resolve();
      } else {
        reject(new Error(`Error instalando dependencias (código: ${code})`));
      }
    });
    
    pip.on('error', (err) => {
      reject(new Error(`Error ejecutando pip: ${err.message}`));
    });
  });
}

function runServer(pythonCmd, serverPath, args) {
  return new Promise((resolve, reject) => {
    log('🚀 Iniciando SiteWise MCP Server...', 'green');
    
    const server = spawn(pythonCmd, [serverPath, ...args], {
      stdio: ['pipe', 'pipe', 'pipe'], // stdio para MCP compatibilidad
      env: {
        ...process.env,
        // Cargar variables de entorno desde .env si existe
        ...loadEnvFile()
      }
    });
    
    server.on('close', (code) => {
      if (code === 0) {
        log('✅ Servidor cerrado correctamente', 'green');
        resolve();
      } else {
        reject(new Error(`Servidor cerrado con código: ${code}`));
      }
    });
    
    server.on('error', (err) => {
      reject(new Error(`Error ejecutando servidor: ${err.message}`));
    });
    
    // Manejo de señales para cerrar gracefully
    process.on('SIGINT', () => {
      log('\n🛑 Cerrando servidor...', 'yellow');
      server.kill('SIGINT');
    });
    
    process.on('SIGTERM', () => {
      log('\n🛑 Cerrando servidor...', 'yellow');
      server.kill('SIGTERM');
    });
  });
}

function loadEnvFile() {
  const envPath = path.join(process.cwd(), '.env');
  const env = {};
  
  if (fs.existsSync(envPath)) {
    const envContent = fs.readFileSync(envPath, 'utf8');
    envContent.split('\n').forEach(line => {
      const [key, value] = line.split('=');
      if (key && value) {
        env[key.trim()] = value.trim();
      }
    });
    log('📋 Variables de entorno cargadas desde .env', 'blue');
  }
  
  return env;
}

async function main() {
  try {
    log('🔧 SiteWise MCP Server', 'cyan');
    log('========================', 'cyan');
    
    // Determinar rutas
    const packageDir = path.dirname(__dirname);
    const srcDir = path.join(packageDir, 'src');
    const serverPath = path.join(srcDir, 'server.py');
    
    // Verificar que el archivo del servidor existe
    if (!fs.existsSync(serverPath)) {
      throw new Error(`No se encontró server.py en ${serverPath}`);
    }
    
    // Verificar Python
    log('🐍 Verificando Python...', 'blue');
    const pythonCmd = await checkPython();
    log(`✅ Python encontrado: ${pythonCmd}`, 'green');
    
    // Verificar argumentos
    const args = process.argv.slice(2);
    const shouldInstall = args.includes('--install') || args.includes('-i');
    const filteredArgs = args.filter(arg => !['--install', '-i'].includes(arg));
    
    // Instalar dependencias si se solicita
    if (shouldInstall) {
      await installDependencies(pythonCmd, srcDir);
    }
    
    // Ejecutar servidor
    await runServer(pythonCmd, serverPath, filteredArgs);
    
  } catch (error) {
    log(`❌ Error: ${error.message}`, 'red');
    process.exit(1);
  }
}

// Mostrar ayuda
if (process.argv.includes('--help') || process.argv.includes('-h')) {
  log('🔧 SiteWise MCP Server', 'cyan');
  log('========================', 'cyan');
  log('');
  log('Uso:', 'bright');
  log('  npx sitewise-mcp-server [opciones]');
  log('');
  log('Opciones:', 'bright');
  log('  -h, --help     Mostrar esta ayuda');
  log('  -i, --install  Instalar dependencias de Python antes de ejecutar');
  log('');
  log('Ejemplos:', 'bright');
  log('  npx sitewise-mcp-server --install');
  log('  npx sitewise-mcp-server');
  log('');
  log('Configuración:', 'bright');
  log('  Crea un archivo .env en el directorio actual con:');
  log('    AWS_PROFILE=default');
  log('    AWS_REGION=us-east-1');
  log('    LOG_LEVEL=DEBUG');
  process.exit(0);
}

main();