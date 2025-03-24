mkdir results
podman run -p 8443:8443 -v $(pwd)/results:/app/results:Z -e ADMIN_PASSWORD=password123 ytdownload
