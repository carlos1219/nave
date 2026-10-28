// Sonda de salud para la imagen `scratch` de Smokescreen, que no trae shell ni `nc`.
// Sale con 0 si el proxy acepta conexiones TCP en el puerto indicado (por defecto 4750).
package main

import (
	"net"
	"os"
	"time"
)

func main() {
	port := "4750"
	if len(os.Args) > 1 {
		port = os.Args[1]
	}
	conn, err := net.DialTimeout("tcp", net.JoinHostPort("127.0.0.1", port), 2*time.Second)
	if err != nil {
		os.Exit(1)
	}
	_ = conn.Close()
}
