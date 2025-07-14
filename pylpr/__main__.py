import sys

# Entrypoint for running as a module: python -m pylpr [client|server] ...
def main():
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):
        print("Usage: python -m pylpr [client|server] [args...]")
        print("  client: Run the print client")
        print("  server: Run the proxy/server")
        print("  args: use --help to get the client and server usage.")
        sys.exit(1)
    mode = sys.argv[1].lower()
    sys.argv = [sys.argv[0]] + sys.argv[2:]  # Remove the mode argument
    if mode == 'client':
        from .client import main as client_main
        client_main()
    elif mode == 'server':
        from .server_proxy import main as server_main
        server_main()
    else:
        print(f"Unknown mode: {mode}")
        print("Usage: python -m pylpr [client|server] [args...]")
        sys.exit(1)

if __name__ == "__main__":
    main()
