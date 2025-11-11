# Solución DHCP en ElementaryOS

## Problema
La interfaz `enp0s8` está en estado `NO-CARRIER` y `DOWN` porque el cable está desconectado (prueba intencional). Necesitas renovar la configuración DHCP cuando vuelvas a conectar el cable, similar a lo que hizo tu amigo en Debian con `ifdown -a` y `ifup -a`.

## Pasos para Renovar DHCP (equivalente a `ifdown -a` y `ifup -a` en Debian)

### Paso 1: Verificar configuración de red en VirtualBox

1. **Asegúrate de que la segunda interfaz de red esté habilitada en VirtualBox:**
   - Abre VirtualBox
   - Selecciona tu máquina virtual de ElementaryOS
   - Ve a Configuración → Red
   - En la pestaña "Adaptador 2" (o "Adaptador 3"):
     - Marca "Habilitar adaptador de red"
     - Tipo de adaptador: "Adaptador de red interna" o "Adaptador NAT"
     - Nombre: Debe coincidir con la red que usa tu servidor DHCP (Rocky Linux)

### Paso 2: Cuando vuelvas a conectar el cable - Renovar DHCP

**En Debian tu amigo usó:**
```bash
sudo ifdown -a
sudo ifup -a
```

**En ElementaryOS (equivalente):**

**Opción A: Usando NetworkManager (recomendado)**
```bash
# Bajar y subir la conexión (equivalente a ifdown/ifup)
sudo nmcli connection down enp0s8
sudo nmcli connection up enp0s8
```

**Opción B: Instalar y usar dhclient (más similar a Debian)**
```bash
# Instalar cliente DHCP
sudo apt update
sudo apt install isc-dhcp-client

# Renovar DHCP (similar al comportamiento de ifup)
sudo dhclient -v enp0s8
```

**Opción C: Usando ip y dhclient directamente**
```bash
# Activar la interfaz
sudo ip link set enp0s8 up

# Solicitar dirección DHCP
sudo dhclient -v enp0s8
```

### Paso 3: Verificar que funcionó

```bash
# Ver la configuración IP
ip a show enp0s8

# Verificar que obtuvo IP del servidor DHCP (debería mostrar 192.168.25.X)
ip addr show enp0s8 | grep "inet "

# Ver el proceso DHCP en tiempo real (similar a lo que viste en Debian)
# Abre otra terminal y ejecuta:
sudo journalctl -u NetworkManager -f

# O si usaste dhclient, verás los mensajes DHCPDISCOVER, DHCPOFFER, etc. directamente
```

## Comparación con Debian

En Debian (como hizo tu amigo), se usan:
- `ifdown -a` y `ifup -a` (comandos tradicionales)

En ElementaryOS (basado en Ubuntu), se usa:
- NetworkManager (`nmcli`) como gestor predeterminado
- Los comandos `ifdown`/`ifup` no están instalados por defecto

