package main

import (
	"flag"
	"fmt"
	"os"
)

const version = "0.1.0"

func main() {
	showVersion := flag.Bool("version", false, "print version and exit")
	flag.Parse()
	if *showVersion {
		fmt.Printf("ringfence-gateway %s\n", version)
		os.Exit(0)
	}
	fmt.Println("ringfence-gateway: run with --version to check the build")
}
