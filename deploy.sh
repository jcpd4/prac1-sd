#!/bin/bash
# Script de despliegue para el sistema distribuido de puntos de recarga EV
# Uso: ./deploy.sh [central|cp1|cp2|all]

set -e

# Colores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Función para imprimir mensajes
print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Verificar que Docker esté instalado
check_docker() {
    if ! command -v docker &> /dev/null; then
        print_error "Docker no está instalado. Por favor, instala Docker primero."
        exit 1
    fi
    
    if ! command -v docker-compose &> /dev/null; then
        print_error "Docker Compose no está instalado. Por favor, instala Docker Compose primero."
        exit 1
    fi
}

# Crear directorios necesarios
create_directories() {
    print_info "Creando directorios necesarios..."
    mkdir -p data logs
    chmod 755 data logs
}

# Construir imagen Docker
build_image() {
    print_info "Construyendo imagen Docker..."
    docker build -t ev-charging-system .
}

# Desplegar Central (PC 1)
deploy_central() {
    print_info "Desplegando Central en PC 1..."
    create_directories
    build_image
    
    # Parar servicios existentes
    docker-compose -f docker-compose-distributed.yml -p pc1 down 2>/dev/null || true
    
    # Iniciar servicios de la Central
    docker-compose -f docker-compose-distributed.yml -p pc1 up -d zookeeper kafka central driver1
    
    print_info "Central desplegada en puerto 8000"
    print_info "Kafka desplegado en puerto 9092"
    print_info "Driver 1 iniciado"
}

# Desplegar CP1 (PC 2)
deploy_cp1() {
    print_info "Desplegando CP1 en PC 2..."
    create_directories
    build_image
    
    # Parar servicios existentes
    docker-compose -f docker-compose-distributed.yml -p pc2 down 2>/dev/null || true
    
    # Iniciar servicios del CP1
    docker-compose -f docker-compose-distributed.yml -p pc2 up -d cp1-engine cp1-monitor driver2
    
    print_info "CP1 Engine desplegado en puerto 8001"
    print_info "CP1 Monitor iniciado"
    print_info "Driver 2 iniciado"
}

# Desplegar CP2 (PC 3)
deploy_cp2() {
    print_info "Desplegando CP2 en PC 3..."
    create_directories
    build_image
    
    # Parar servicios existentes
    docker-compose -f docker-compose-distributed.yml -p pc3 down 2>/dev/null || true
    
    # Iniciar servicios del CP2
    docker-compose -f docker-compose-distributed.yml -p pc3 up -d cp2-engine cp2-monitor
    
    print_info "CP2 Engine desplegado en puerto 8002"
    print_info "CP2 Monitor iniciado"
}

# Desplegar todo en un solo PC (para pruebas)
deploy_all() {
    print_info "Desplegando todo el sistema en un solo PC (modo prueba)..."
    create_directories
    build_image
    
    # Parar servicios existentes
    docker-compose down 2>/dev/null || true
    
    # Iniciar todos los servicios
    docker-compose up -d
    
    print_info "Sistema completo desplegado"
    print_info "Central: puerto 8000"
    print_info "CP1 Engine: puerto 8001"
    print_info "CP2 Engine: puerto 8002"
    print_info "Kafka: puerto 9092"
}

# Mostrar logs
show_logs() {
    local service=$1
    if [ -z "$service" ]; then
        print_info "Mostrando logs de todos los servicios..."
        docker-compose logs -f
    else
        print_info "Mostrando logs de $service..."
        docker-compose logs -f $service
    fi
}

# Parar servicios
stop_services() {
    print_info "Parando servicios..."
    docker-compose down
    docker-compose -f docker-compose-distributed.yml -p pc1 down 2>/dev/null || true
    docker-compose -f docker-compose-distributed.yml -p pc2 down 2>/dev/null || true
    docker-compose -f docker-compose-distributed.yml -p pc3 down 2>/dev/null || true
}

# Limpiar sistema
clean_system() {
    print_warning "Limpiando sistema Docker..."
    docker-compose down -v
    docker-compose -f docker-compose-distributed.yml -p pc1 down -v 2>/dev/null || true
    docker-compose -f docker-compose-distributed.yml -p pc2 down -v 2>/dev/null || true
    docker-compose -f docker-compose-distributed.yml -p pc3 down -v 2>/dev/null || true
    docker system prune -f
}

# Mostrar ayuda
show_help() {
    echo "Uso: $0 [comando]"
    echo ""
    echo "Comandos disponibles:"
    echo "  central    - Desplegar Central en PC 1"
    echo "  cp1        - Desplegar CP1 en PC 2"
    echo "  cp2        - Desplegar CP2 en PC 3"
    echo "  all        - Desplegar todo en un PC (modo prueba)"
    echo "  logs       - Mostrar logs de todos los servicios"
    echo "  logs [svc] - Mostrar logs de un servicio específico"
    echo "  stop       - Parar todos los servicios"
    echo "  clean      - Limpiar sistema Docker"
    echo "  help       - Mostrar esta ayuda"
    echo ""
    echo "Ejemplos:"
    echo "  $0 central    # Desplegar Central"
    echo "  $0 logs       # Ver logs"
    echo "  $0 stop       # Parar servicios"
}

# Función principal
main() {
    case "${1:-help}" in
        central)
            check_docker
            deploy_central
            ;;
        cp1)
            check_docker
            deploy_cp1
            ;;
        cp2)
            check_docker
            deploy_cp2
            ;;
        all)
            check_docker
            deploy_all
            ;;
        logs)
            show_logs $2
            ;;
        stop)
            stop_services
            ;;
        clean)
            clean_system
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            print_error "Comando desconocido: $1"
            show_help
            exit 1
            ;;
    esac
}

# Ejecutar función principal
main "$@"

