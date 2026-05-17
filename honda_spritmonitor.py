import os
from pathlib import Path
import datetime
import logging
import requests
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv

from pymyhondaplus import HondaAPI, HondaAuth, parse_ev_status, get_storage
from pymyhondaplus.auth import DeviceKey

logger = logging.getLogger(__name__)

class SpritmonitorAPI:
    """
    Wrapper for the Spritmonitor REST API.
    """
    BASE_URL = "https://api.spritmonitor.de/v1"

    def __init__(self, application_id: str, bearer_token: str):
        self.application_id = application_id
        self.bearer_token = bearer_token
        self.session = requests.Session()
        self.session.headers.update({
            "APPLICATION-ID": self.application_id,
            "Authorization": f"Bearer {self.bearer_token}",
            "Accept": "application/json"
        })

    def get_vehicles(self) -> List[Dict]:
        resp = self.session.get(f"{self.BASE_URL}/vehicles.json")
        resp.raise_for_status()
        return resp.json()

    def get_tanks(self, vehicle_id: int) -> List[Dict]:
        resp = self.session.get(f"{self.BASE_URL}/vehicle/{vehicle_id}/tanks.json")
        resp.raise_for_status()
        return resp.json()

    def get_fuelings(self, vehicle_id: int, limit: int = 15) -> List[Dict]:
        resp = self.session.get(
            f"{self.BASE_URL}/vehicle/{vehicle_id}/fuelings.json",
            params={"limit": limit}
        )
        resp.raise_for_status()
        return resp.json()

    def add_fueling(self, vehicle_id: int, tank_id: int, date_str: str, 
                    trip: float, quantity: float, fueling_type: str, 
                    fuelsortid: int, quantityunitid: int, odometer: Optional[float] = None, 
                    **kwargs) -> bool:
        """
        Add a fueling entry. 
        Note: The Spritmonitor API uses GET requests for adding fuelings.
        """
        params = {
            "date": date_str,  # format DD.MM.YYYY
            "trip": trip,
            "quantity": quantity,
            "type": fueling_type,  # 'full', 'notfull', 'first', 'invalid'
            "fuelsortid": fuelsortid,
            "quantityunitid": quantityunitid
        }
        if odometer is not None:
            params["odometer"] = odometer
            
        params.update(kwargs)
        
        resp = self.session.get(f"{self.BASE_URL}/vehicle/{vehicle_id}/tank/{tank_id}/fueling.json", params=params)
        resp.raise_for_status()
        return resp.status_code == 200


class HondaToSpritmonitor:
    """
    Adapter that reads data from a Honda vehicle via pymyhondaplus and syncs it to Spritmonitor.
    """
    def __init__(self, honda_email: Optional[str] = None, honda_password: Optional[str] = None, 
                 spritmonitor_app_id: Optional[str] = None, spritmonitor_bearer: Optional[str] = None,
                 spritmonitor_vehicle_id: Optional[int] = None, env_file: Optional[Path] = None,
                 allow_login: bool = False):
        
        # Load sprint monitor config from ~/.env_sprintmonitor by default
        if env_file is None:
            env_file = Path.home() / ".env_sprintmonitor"
            
        if env_file.exists():
            load_dotenv(env_file)
            
        self.honda_email = honda_email or os.environ.get("HONDA_EMAIL")
        self.honda_password = honda_password or os.environ.get("HONDA_PASSWORD")
        self.allow_login = allow_login
        
        # Setup token and key file paths
        token_file = Path(os.environ.get(
            "HONDA_TOKEN_FILE",
            Path.home() / ".honda_tokens.json",
        ))
        key_file = Path(os.environ.get(
            "HONDA_KEY_FILE",
            Path.home() / ".honda_device_key.pem",
        ))
        
        # Ensure they are in the environment so pymyhondaplus can use them
        os.environ["HONDA_TOKEN_FILE"] = str(token_file)
        os.environ["HONDA_KEY_FILE"] = str(key_file)
        
        sm_app_id = spritmonitor_app_id or os.environ.get("SPRITMONITOR_APP_ID")
        sm_bearer = spritmonitor_bearer or os.environ.get("SPRITMONITOR_BEARER")
        
        # Default vehicle ID
        sm_vehicle_id_env = os.environ.get("SPRITMONITOR_VEHICLE_ID")
        self.spritmonitor_vehicle_id = spritmonitor_vehicle_id or (int(sm_vehicle_id_env) if sm_vehicle_id_env else None)
        
        if not sm_app_id or not sm_bearer:
            raise ValueError("Spritmonitor credentials missing! Provide them or set them in ~/.env_sprintmonitor")
            
        self.spritmonitor = SpritmonitorAPI(sm_app_id, sm_bearer)
        self.honda_api = None

    def authenticate_honda(self):
        """Authenticates with the Honda Connect API."""
        try:
            token_file = Path(os.environ.get("HONDA_TOKEN_FILE"))
            key_file = Path(os.environ.get("HONDA_KEY_FILE"))
            storage = get_storage(token_file, key_file)
            tokens = storage.load_tokens()
            
            if not tokens:
                raise ValueError("No tokens found in storage")
                
            self.honda_api = HondaAPI()
            
            valid_keys = {"access_token", "refresh_token", "expires_in", "personal_id", "user_id", "vehicles"}
            filtered_tokens = {k: v for k, v in tokens.items() if k in valid_keys}
            self.honda_api.set_tokens(**filtered_tokens)
            
            # Test the API to see if tokens are valid by getting profile
            self.honda_api.get_user_profile()
            logger.info("Authenticated with Honda API via saved tokens successfully.")
        except Exception as e:
            logger.info(f"Could not authenticate via saved tokens: {e}")
            if not self.allow_login:
                raise ValueError("Honda tokens missing or expired. Run with --allow-login and ensure HONDA_EMAIL and HONDA_PASSWORD are set in your environment to re-login.")
            if not self.honda_email or not self.honda_password:
                raise ValueError("Honda tokens missing or expired, but no email/password provided for login.")
            logger.info("Attempting full login with email and password...")
            
            token_file = Path(os.environ.get("HONDA_TOKEN_FILE"))
            key_file = Path(os.environ.get("HONDA_KEY_FILE"))
            storage = get_storage(token_file, key_file)
            device_key_bytes = storage.load_device_key()
            
            device_key_obj = DeviceKey(pem_data=device_key_bytes) if device_key_bytes else DeviceKey()
            auth = HondaAuth(device_key_obj)
            tokens = auth.full_login(self.honda_email, self.honda_password)
            
            # Save the newly generated tokens so they can be reused next time
            storage.save_tokens(tokens)
            if not device_key_bytes:
                storage.save_device_key(auth.device_key.pem_bytes)
                
            self.honda_api = HondaAPI()
            
            # Filter tokens to only include what set_tokens accepts
            valid_keys = {"access_token", "refresh_token", "expires_in", "personal_id", "user_id", "vehicles"}
            filtered_tokens = {k: v for k, v in tokens.items() if k in valid_keys}
            
            self.honda_api.set_tokens(**filtered_tokens)
            logger.info("Authenticated with Honda API via email/password successfully.")

    def get_honda_odometer(self, vin: str) -> float:
        """Retrieves the current odometer reading from the Honda vehicle."""
        if not self.honda_api:
            self.authenticate_honda()
        
        dashboard = self.honda_api.get_dashboard(vin)
        ev_status = parse_ev_status(dashboard)
        
        # Pymyhondaplus EVStatus parsing typically maps the odometer.
        if hasattr(ev_status, 'odometer') and ev_status.odometer is not None:
            return float(ev_status.odometer)
        # Fallback to dictionary lookup if accessing raw dict
        elif isinstance(dashboard, dict) and 'odometer' in dashboard:
            return float(dashboard['odometer'])
        elif isinstance(ev_status, dict) and 'odometer' in ev_status:
            return float(ev_status['odometer'])
        else:
            raise ValueError("Could not find odometer in vehicle dashboard data.")

    def _get_tank_id(self, vehicle_id: int, fuel_type: int) -> int:
        """
        Retrieves the tank ID for a given vehicle based on the fuel type.
        fuel_type: 2 for Gasoline, 5 for Electricity.
        """
        tanks = self.spritmonitor.get_tanks(vehicle_id)
        for tank in tanks:
            if tank.get("fuelsorttype") == fuel_type:
                return tank["id"]
        raise ValueError(f"Could not find a tank with fuelsorttype {fuel_type} for vehicle {vehicle_id}")

    def sync_refueling(self, liters: float,
                     honda_vin: Optional[str] = None, spritmonitor_vehicle_id: Optional[int] = None, 
                     fueling_type: str = "full", trip: Optional[float] = None, 
                     fuelsortid: int = 7, **kwargs) -> bool:
        """
        Syncs a gasoline/fuel refueling entry to Spritmonitor using the current Honda odometer.
        """
        sm_vehicle_id = spritmonitor_vehicle_id or self.spritmonitor_vehicle_id
        if not sm_vehicle_id:
            raise ValueError("spritmonitor_vehicle_id not provided and SPRITMONITOR_VEHICLE_ID is empty.")
            
        tank_id = self._get_tank_id(sm_vehicle_id, 2)  # 2 = Gasoline
        
        return self._sync_entry(spritmonitor_tank_id=tank_id, quantity=liters,
                                honda_vin=honda_vin, spritmonitor_vehicle_id=sm_vehicle_id,
                                fueling_type=fueling_type, trip=trip, fuelsortid=fuelsortid, quantityunitid=1, **kwargs)

    def sync_charging(self, kwh: float,
                     honda_vin: Optional[str] = None, spritmonitor_vehicle_id: Optional[int] = None, 
                     fueling_type: str = "full", trip: Optional[float] = None, 
                     fuelsortid: int = 24, **kwargs) -> bool:
        """
        Syncs an electric charging entry to Spritmonitor using the current Honda odometer.
        Quantity unit is automatically set to kWh (5).
        """
        sm_vehicle_id = spritmonitor_vehicle_id or self.spritmonitor_vehicle_id
        if not sm_vehicle_id:
            raise ValueError("spritmonitor_vehicle_id not provided and SPRITMONITOR_VEHICLE_ID is empty.")
            
        tank_id = self._get_tank_id(sm_vehicle_id, 5)  # 5 = Electricity
        
        return self._sync_entry(spritmonitor_tank_id=tank_id, quantity=kwh,
                                honda_vin=honda_vin, spritmonitor_vehicle_id=sm_vehicle_id,
                                fueling_type=fueling_type, trip=trip, fuelsortid=fuelsortid, quantityunitid=5, **kwargs)

    def _sync_entry(self, spritmonitor_tank_id: int, quantity: float,
                     honda_vin: Optional[str] = None, spritmonitor_vehicle_id: Optional[int] = None, 
                     fueling_type: str = "full", trip: Optional[float] = None, 
                     fuelsortid: int = 7, quantityunitid: int = 1, **kwargs) -> bool:
        """
        Internal method to sync an entry to Spritmonitor.
        """
        
        honda_vin = honda_vin or os.environ.get("HONDA_VIN")
        if not honda_vin:
            raise ValueError("honda_vin not provided and HONDA_VIN environment variable is empty.")
            
        sm_vehicle_id = spritmonitor_vehicle_id or self.spritmonitor_vehicle_id
        if not sm_vehicle_id:
            raise ValueError("spritmonitor_vehicle_id not provided and SPRITMONITOR_VEHICLE_ID is empty.")
            
        current_odometer = self.get_honda_odometer(honda_vin)
        logger.info(f"Current Honda odometer for {honda_vin}: {current_odometer}")
        
        if trip is None:
            logger.info("Trip distance not provided. Attempting to calculate from last Spritmonitor entry...")
            fuelings = self.spritmonitor.get_fuelings(sm_vehicle_id, limit=1)
            
            if fuelings and 'odometer' in fuelings[0] and fuelings[0]['odometer'] is not None:
                last_odometer = float(fuelings[0]['odometer'])
                trip = current_odometer - last_odometer
                if trip < 0:
                    trip = 0.0
                logger.info(f"Calculated trip: {trip} (Current: {current_odometer} - Last: {last_odometer})")
            else:
                raise ValueError("Could not determine trip automatically. Please provide 'trip' manually or ensure a previous fueling with an odometer exists in Spritmonitor.")

        date_str = datetime.datetime.now().strftime("%d.%m.%Y")
        
        logger.info(f"Adding fueling to Spritmonitor: tank_id={spritmonitor_tank_id}, date={date_str}, trip={trip}, quantity={quantity}, type={fueling_type}")
        return self.spritmonitor.add_fueling(
            vehicle_id=sm_vehicle_id,
            tank_id=spritmonitor_tank_id,
            date_str=date_str,
            trip=trip,
            quantity=quantity,
            fueling_type=fueling_type,
            fuelsortid=fuelsortid,
            quantityunitid=quantityunitid,
            odometer=current_odometer,
            **kwargs
        )

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Honda CRV to Spritmonitor Adapter")
    
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--electricity", type=float, help="Amount of electricity added in kWh")
    group.add_argument("--gasoline", type=float, help="Amount of gasoline added in liters")
    
    parser.add_argument("--vin", type=str, help="Honda VIN or nickname (defaults to HONDA_VIN in env)")
    parser.add_argument("--vehicle-id", type=int, help="Spritmonitor Vehicle ID (defaults to SPRITMONITOR_VEHICLE_ID in env)")
    parser.add_argument("--type", type=str, default="full", choices=["full", "notfull", "first"], help="Fueling type")
    parser.add_argument("--allow-login", action="store_true", help="Allow full login with email and password if tokens are expired")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    
    args = parser.parse_args()
    
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    
    try:
        adapter = HondaToSpritmonitor(allow_login=args.allow_login)
        if args.electricity is not None:
            adapter.sync_charging(
                kwh=args.electricity,
                honda_vin=args.vin,
                spritmonitor_vehicle_id=args.vehicle_id,
                fueling_type=args.type
            )
            print("Successfully synced charging entry to Spritmonitor!")
        elif args.gasoline is not None:
            adapter.sync_refueling(
                liters=args.gasoline,
                honda_vin=args.vin,
                spritmonitor_vehicle_id=args.vehicle_id,
                fueling_type=args.type
            )
            print("Successfully synced refueling entry to Spritmonitor!")
    except Exception as e:
        logger.error(f"Sync failed: {e}")
        exit(1)
