@echo off
REM Script de despliegue para Windows
REM Uso: deploy.bat [central|cp1|cp2|all]

setlocal enabledelayedexpansion

REM Colores (limitados en batch)
set "INFO=[INFO]"
set "WARNING=[WARNING]"
set "ERROR=[ERROR]"

REM Función para imprimir mensajes
:print_info
echo %INFO% %~1
goto :eof

:print_warning
echo %WARNING% %~1
goto :eof

:print_error
echo %ERROR% %~1
goto :eof

REM Verificar que Docker esté instalado
:check_docker
docker --version >nul 2>&1
if errorlevel 1 (
    call :print_error "Docker no está instalado. Por favor, instala Docker Desktop primero."
    exit /b 1
)

docker-compose --version >nul 2>&1
if errorlevel 1 (
    call :print_error "Docker Compose no está instalado. Por favor, instala Docker Compose primero."
    exit /b 1
)
goto :eof

REM Crear directorios necesarios
:create_directories
call :print_info "Creando directorios necesarios..."
if not exist "data" mkdir data
if not exist "logs" mkdir logs
goto :eof

REM Construir imagen Docker
:build_image
call :print_info "Construyendo imagen Docker..."
docker build -t ev-charging-system .
goto :eof

REM Desplegar Central (PC 1)
:deploy_central
call :print_info "Desplegando Central en PC 1..."
call :create_directories
call :build_image

REM Parar servicios existentes
docker-compose -f docker-compose-distributed.yml -p pc1 down 2>nul

REM Iniciar servicios de la Central
docker-compose -f docker-compose-distributed.yml -p pc1 up -d zookeeper kafka central driver1

call :print_info "Central desplegada en puerto 8000"
call :print_info "Kafka desplegado en puerto 9092"
call :print_info "Driver 1 iniciado"
goto :eof

REM Desplegar CP1 (PC 2)
:deploy_cp1
call :print_info "Desplegando CP1 en PC 2..."
call :create_directories
call :build_image

REM Parar servicios existentes
docker-compose -f docker-compose-distributed.yml -p pc2 down 2>nul

REM Iniciar servicios del CP1
docker-compose -f docker-compose-distributed.yml -p pc2 up -d cp1-engine cp1-monitor driver2

call :print_info "CP1 Engine desplegado en puerto 8001"
call :print_info "CP1 Monitor iniciado"
call :print_info "Driver 2 iniciado"
goto :eof

REM Desplegar CP2 (PC 3)
:deploy_cp2
call :print_info "Desplegando CP2 en PC 3..."
call :create_directories
call :build_image

REM Parar servicios existentes
docker-compose -f docker-compose-distributed.yml -p pc3 down 2>nul

REM Iniciar servicios del CP2
docker-compose -f docker-compose-distributed.yml -p pc3 up -d cp2-engine cp2-monitor

call :print_info "CP2 Engine desplegado en puerto 8002"
call :print_info "CP2 Monitor iniciado"
goto :eof

REM Desplegar todo en un solo PC (para pruebas)
:deploy_all
call :print_info "Desplegando todo el sistema en un solo PC (modo prueba)..."
call :create_directories
call :build_image

REM Parar servicios existentes
docker-compose down 2>nul

REM Iniciar todos los servicios
docker-compose up -d

call :print_info "Sistema completo desplegado"
call :print_info "Central: puerto 8000"
call :print_info "CP1 Engine: puerto 8001"
call :print_info "CP2 Engine: puerto 8002"
call :print_info "Kafka: puerto 9092"
goto :eof

REM Mostrar logs
:show_logs
if "%2"=="" (
    call :print_info "Mostrando logs de todos los servicios..."
    docker-compose logs -f
) else (
    call :print_info "Mostrando logs de %2..."
    docker-compose logs -f %2
)
goto :eof

REM Parar servicios
:stop_services
call :print_info "Parando servicios..."
docker-compose down
docker-compose -f docker-compose-distributed.yml -p pc1 down 2>nul
docker-compose -f docker-compose-distributed.yml -p pc2 down 2>nul
docker-compose -f docker-compose-distributed.yml -p pc3 down 2>nul
goto :eof

REM Limpiar sistema
:clean_system
call :print_warning "Limpiando sistema Docker..."
docker-compose down -v
docker-compose -f docker-compose-distributed.yml -p pc1 down -v 2>nul
docker-compose -f docker-compose-distributed.yml -p pc2 down -v 2>nul
docker-compose -f docker-compose-distributed.yml -p pc3 down -v 2>nul
docker system prune -f
goto :eof

REM Mostrar ayuda
:show_help
echo Uso: %0 [comando]
echo.
echo Comandos disponibles:
echo   central    - Desplegar Central en PC 1
echo   cp1        - Desplegar CP1 en PC 2
echo   cp2        - Desplegar CP2 en PC 3
echo   all        - Desplegar todo en un PC (modo prueba)
echo   logs       - Mostrar logs de todos los servicios
echo   logs [svc] - Mostrar logs de un servicio específico
echo   stop       - Parar todos los servicios
echo   clean      - Limpiar sistema Docker
echo   help       - Mostrar esta ayuda
echo.
echo Ejemplos:
echo   %0 central    # Desplegar Central
echo   %0 logs       # Ver logs
echo   %0 stop       # Parar servicios
goto :eof

REM Función principal
:main
if "%1"=="central" (
    call :check_docker
    if errorlevel 1 exit /b 1
    call :deploy_central
) else if "%1"=="cp1" (
    call :check_docker
    if errorlevel 1 exit /b 1
    call :deploy_cp1
) else if "%1"=="cp2" (
    call :check_docker
    if errorlevel 1 exit /b 1
    call :deploy_cp2
) else if "%1"=="all" (
    call :check_docker
    if errorlevel 1 exit /b 1
    call :deploy_all
) else if "%1"=="logs" (
    call :show_logs %2
) else if "%1"=="stop" (
    call :stop_services
) else if "%1"=="clean" (
    call :clean_system
) else if "%1"=="help" (
    call :show_help
) else if "%1"=="--help" (
    call :show_help
) else if "%1"=="-h" (
    call :show_help
) else if "%1"=="" (
    call :show_help
) else (
    call :print_error "Comando desconocido: %1"
    call :show_help
    exit /b 1
)

REM Ejecutar función principal
call :main %1 %2

