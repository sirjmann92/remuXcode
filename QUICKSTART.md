# Quick Start Guide

## Installation Steps

1. **Install the service**:
```bash
cd ~/Projects/dts-conversion

# Make service executable
chmod +x dts_webhook_service.py

# Copy systemd service file
sudo cp dts-converter-webhook.service /etc/systemd/system/

# Create log file
sudo touch /var/log/dts-converter-webhook.log
sudo chown $USER:$USER /var/log/dts-converter-webhook.log

# Start and enable service
sudo systemctl daemon-reload
sudo systemctl enable dts-converter-webhook
sudo systemctl start dts-converter-webhook
```

2. **Verify it's running**:
```bash
# Check service status
sudo systemctl status dts-converter-webhook

# Test health endpoint
curl http://localhost:7889/health

# Expected response:
# {"status": "healthy", "service": "dts-converter-webhook"}
```

3. **Configure Sonarr**:
   - Go to **Settings → Connect → Add → Webhook**
   - Name: `DTS Audio Converter`
   - On Import Complete: ✓
   - URL: `http://YOUR_HOST_IP:7889/`
   - Method: POST
   - Click **Test** - should succeed
   - Click **Save**

4. **Configure Radarr** (same as above but):
   - On Import: ✓
   - On Upgrade: ✓
   - On Import Complete: (doesn't exist in Radarr)

5. **Watch it work**:
```bash
# Monitor logs
sudo journalctl -u dts-converter-webhook -f

# Trigger a test by importing a file with DTS audio
```

## Troubleshooting

**Service won't start?**
```bash
# Check for errors
sudo journalctl -u dts-converter-webhook -n 50

# Make sure .env file exists
ls -la ~/Projects/dts-conversion/.env
```

**Webhook test fails?**
```bash
# Check if service is listening
sudo netstat -tlnp | grep 7889

# Test manually
curl http://localhost:7889/health
```

**Need to restart after changes?**
```bash
sudo systemctl restart dts-converter-webhook
```
