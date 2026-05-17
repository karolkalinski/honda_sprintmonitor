# Honda to Spritmonitor Adapter

This library provides an adapter to synchronize data from your Honda CRV (via the My Honda+ app) to Spritmonitor. It utilizes `pymyhondaplus` to fetch your vehicle's odometer and `requests` to log a refueling/charging session to Spritmonitor's API.

## Requirements

1. Python 3.9+
2. A Honda Connect account.
3. A Spritmonitor account with:
   - Application-ID
   - Bearer Token

Install the dependencies:

```bash
pip install -r requirements.txt
```

## Configuration

A template configuration file has been provided: `.env_sprintmonitor.example`.
Copy this file to your home directory or your project root and rename it to `.env_sprintmonitor`:

```bash
cp .env_sprintmonitor.example ~/.env_sprintmonitor
```

Fill out your credentials in the newly created `.env_sprintmonitor` file.

## Usage as a Library

You can easily embed `HondaToSpritmonitor` into your own scripts or Home Assistant automations.

```python
import logging
import os
from honda_spritmonitor import HondaToSpritmonitor

# Optional: Enable logging to see what's happening
logging.basicConfig(level=logging.INFO)

# The adapter will automatically load:
# 1. SPRITMONITOR_APP_ID, SPRITMONITOR_BEARER, and SPRITMONITOR_VEHICLE_ID from ~/.env_sprintmonitor
# 2. Honda tokens from ~/.honda_tokens.json and ~/.honda_device_key.pem 
#    (or whatever is defined in your environment as HONDA_TOKEN_FILE)

# It's recommended to also add HONDA_VIN to your environment or ~/.env_sprintmonitor
os.environ["HONDA_VIN"] = "YOUR_HONDA_VIN_OR_NICKNAME"
SPRITMONITOR_TANK_ID = 1

# 1. Initialize the adapter
adapter = HondaToSpritmonitor()

# 2. Sync a refueling session
# Example: You just added 35.5 liters of fuel. 
# The script will auto-fetch your car's odometer and calculate the trip distance.
try:
    success = adapter.sync_refueling(
        spritmonitor_tank_id=SPRITMONITOR_TANK_ID,
        quantity=35.5,
        fueling_type="full", # 'full', 'notfull', or 'first'
        fuelsortid=7,        # E.g. 7 for Gasoline, 5 for Electricity, etc.
        quantityunitid=1,    # E.g. 1 for Liters, 5 for kWh
        
        # Optional: you can pass any other Spritmonitor parameter here
        # price=55.0,
        # note="Refueled at Shell"
    )
    if success:
        print("Refueling synced successfully!")
except Exception as e:
    print(f"Failed to sync: {e}")
```

### Usage as a CLI Script

You can also run the adapter directly from your terminal! Just make sure your `~/.env_sprintmonitor` contains the `SPRITMONITOR_APP_ID`, `SPRITMONITOR_BEARER`, `SPRITMONITOR_VEHICLE_ID`, and `HONDA_VIN`.

```bash
python honda_spritmonitor.py --tank-id 1 --quantity 35.5 --type full
```

Use `python honda_spritmonitor.py --help` for a full list of available options.

### Auto-calculating `trip`
Spritmonitor's API requires a `trip` value (the distance driven since the last fill-up) to calculate consumption. If you do not provide the `trip` argument to `sync_refueling`, the library will automatically:
1. Fetch your car's current odometer from Honda.
2. Fetch your last logged fueling's odometer from Spritmonitor.
3. Calculate the difference and pass it as the `trip`.

## Additional Methods

You can also use the underlying API wrappers directly if you need custom logic:
- `adapter.get_honda_odometer(vin)`: Retrieves only the odometer.
- `adapter.spritmonitor.get_vehicles()`: Lists your Spritmonitor vehicles to find your vehicle IDs.
- `adapter.spritmonitor.get_tanks(vehicle_id)`: Lists the tanks for a vehicle to find tank IDs.
