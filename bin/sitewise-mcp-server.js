#!/usr/bin/env node

const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

// CRÍTICO: Solo logs a stderr, nunca a stdout (MCP requirement)
function log(message) {
  console.error(`[sitewise-mcp] ${message}`);
}

function checkPython() {
  return new Promise((resolve, reject) => {
    const python = spawn('python3', ['--version'], { stdio: ['pipe', 'pipe', 'pipe'] });
    
    python.on('close', (code) => {
      if (code === 0) {
        resolve('python3');
      } else {
        const pythonAlt = spawn('python', ['--version'], { stdio: ['pipe', 'pipe', 'pipe'] });
        pythonAlt.on('close', (altCode) => {
          if (altCode === 0) {
            resolve('python');
          } else {
            reject(new Error('Python no encontrado'));
          }
        });
      }
    });
    
    python.on('error', () => {
      const pythonAlt = spawn('python', ['--version'], { stdio: ['pipe', 'pipe', 'pipe'] });
      pythonAlt.on('close', (altCode) => {
        if (altCode === 0) {
          resolve('python');
        } else {
          reject(new Error('Python no encontrado'));
        }
      });
      pythonAlt.on('error', () => {
        reject(new Error('Python no encontrado'));
      });
    });
  });
}

function installDependencies(pythonCmd, srcDir) {
  return new Promise((resolve, reject) => {
    const requirementsPath = path.join(srcDir, 'requirements.txt');
    
    if (!fs.existsSync(requirementsPath)) {
      reject(new Error(`requirements.txt no encontrado en ${requirementsPath}`));
      return;
    }
    
    log('Instalando dependencias...');
    const pip = spawn(pythonCmd, ['-m', 'pip', 'install', '-r', requirementsPath], {
      stdio: ['pipe', 'pipe', 'inherit'] // Solo stderr visible
    });
    
    pip.on('close', (code) => {
      if (code === 0) {
        resolve();
      } else {
        reject(new Error(`Error instalando dependencias: ${code}`));
      }
    });
    
    pip.on('error', (err) => {
      reject(new Error(`Error pip: ${err.message}`));
    });
  });
}

function runServer(pythonCmd, serverPath) {
  return new Promise((resolve, reject) => {
    log('Iniciando servidor...');
    
    // CRÍTICO: stdio: 'inherit' para que MCP funcione correctamente
    const server = spawn(pythonCmd, [serverPath], {
      stdio: 'inherit',
      env: {
        ...process.env,
        ...loadEnvFile(),
        // Forzar que Python use stderr para logs
        PYTHONUNBUFFERED: '1'
      }
    });
    
    server.on('close', (code) => {
      if (code === 0) {
        resolve();
      } else {
        reject(new Error(`Servidor terminado: ${code}`));
      }
    });
    
    server.on('error', (err) => {
      reject(new Error(`Error servidor: ${err.message}`));
    });
    
    // Manejo de señales
    process.on('SIGINT', () => {
      server.kill('SIGINT');
    });
    
    process.on('SIGTERM', () => {
      server.kill('SIGTERM');
    });
  });
}

function loadEnvFile() {
  const envPath = path.join(process.cwd(), '.env');
  const env = {};
  
  if (fs.existsSync(envPath)) {
    try {
      const envContent = fs.readFileSync(envPath, 'utf8');
      envContent.split('\n').forEach(line => {
        const [key, value] = line.split('=');
        if (key && value) {
          env[key.trim()] = value.trim().replace(/^["']|["']$/g, '');
        }
      });
    } catch (err) {
      log(`Error leyendo .env: ${err.message}`);
    }
  }
  
  return env;
}

async function main() {
  try {
    // Rutas
    const packageDir = path.dirname(__dirname);
    const srcDir = path.join(packageDir, 'src');
    const serverPath = path.join(srcDir, 'server.py');
    
    if (!fs.existsSync(serverPath)) {
      throw new Error(`server.py no encontrado en ${serverPath}`);
    }
    
    // Verificar Python (silencioso)
    const pythonCmd = await checkPython();
    
    // Argumentos
    const args = process.argv.slice(2);
    const shouldInstall = args.includes('--install') || args.includes('-i');
    
    // Instalar dependencias si se solicita
    if (shouldInstall) {
      await installDependencies(pythonCmd, srcDir);
    }
    
    // Ejecutar servidor
    await runServer(pythonCmd, serverPath);
    
  } catch (error) {
    log(`ERROR: ${error.message}`);
    process.exit(1);
  }
}

// Help
if (process.argv.includes('--help') || process.argv.includes('-h')) {
  console.error('SiteWise MCP Server');
  console.error('Uso: npx sitewise-mcp-server [--install]');
  console.error('  --install: Instalar dependencias Python');
  process.exit(0);
}

main();