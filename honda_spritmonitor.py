import os
from pathlib import Path
import datetime
import logging
import requests
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv

from pymyhondaplus import HondaAPI, HondaAuth, parse_ev_status

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
                 spritmonitor_vehicle_id: Optional[int] = None, env_file: Optional[Path] = None):
        
        # Load sprint monitor config from ~/.env_sprintmonitor by default
        if env_file is None:
            env_file = Path.home() / ".env_sprintmonitor"
            
        if env_file.exists():
            load_dotenv(env_file)
            
        self.honda_email = honda_email or os.environ.get("HONDA_EMAIL")
        self.honda_password = honda_password or os.environ.get("HONDA_PASSWORD")
        
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
            # If tokens are already in the file, we might not need full_login. 
            # We can just initialize HondaAPI. It should pick up the token files automatically.
            self.honda_api = HondaAPI()
            # Test the API to see if tokens are valid by getting profile
            self.honda_api.get_user_profile()
            logger.info("Authenticated with Honda API via saved tokens successfully.")
        except Exception as e:
            logger.info(f"Could not authenticate via saved tokens: {e}")
            if not self.honda_email or not self.honda_password:
                raise ValueError("Honda tokens missing or invalid, and no email/password provided for login.")
            logger.info("Attempting full login with email and password...")
            auth = HondaAuth()
            tokens = auth.full_login(self.honda_email, self.honda_password)
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

    def sync_refueling(self, spritmonitor_tank_id: int, quantity: float,
                     honda_vin: Optional[str] = None, spritmonitor_vehicle_id: Optional[int] = None, 
                     fueling_type: str = "full", trip: Optional[float] = None, 
                     fuelsortid: int = 7, quantityunitid: int = 1, **kwargs) -> bool:
        """
        Syncs a refueling or charging entry to Spritmonitor using the current Honda odometer.
        
        If 'trip' is not provided, it will automatically query the last Spritmonitor fueling
        and calculate the trip distance based on the current Honda odometer.
        
        Args:
            spritmonitor_tank_id (int): Spritmonitor tank ID.
            quantity (float): Amount of fuel/electricity added.
            honda_vin (str, optional): Your Honda VIN or nickname. Defaults to HONDA_VIN env var.
            spritmonitor_vehicle_id (int, optional): Spritmonitor vehicle ID. Defaults to env var or constructor.
            fueling_type (str): 'full', 'notfull', or 'first'. Default 'full'.
            trip (float, optional): Trip distance since last refueling. If None, auto-calculated.
            fuelsortid (int): Spritmonitor fuel sort ID (e.g. 7).
            quantityunitid (int): Spritmonitor quantity unit ID (1=Liter, 5=kWh).
            **kwargs: Additional parameters for Spritmonitor API (price, note, etc.)
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
        
        logger.info(f"Adding fueling to Spritmonitor: date={date_str}, trip={trip}, quantity={quantity}, type={fueling_type}")
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
    parser.add_argument("--tank-id", type=int, required=True, help="Spritmonitor Tank ID (e.g. 1)")
    parser.add_argument("--quantity", type=float, required=True, help="Amount of fuel/electricity added")
    parser.add_argument("--vin", type=str, help="Honda VIN or nickname (defaults to HONDA_VIN in env)")
    parser.add_argument("--vehicle-id", type=int, help="Spritmonitor Vehicle ID (defaults to SPRITMONITOR_VEHICLE_ID in env)")
    parser.add_argument("--type", type=str, default="full", choices=["full", "notfull", "first"], help="Fueling type")
    parser.add_argument("--fuelsort", type=int, default=7, help="Fuel sort ID (default 7)")
    parser.add_argument("--unit", type=int, default=1, help="Quantity unit ID (1=Liter, 5=kWh, default 1)")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    
    args = parser.parse_args()
    
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    
    try:
        adapter = HondaToSpritmonitor()
        adapter.sync_refueling(
            spritmonitor_tank_id=args.tank_id,
            quantity=args.quantity,
            honda_vin=args.vin,
            spritmonitor_vehicle_id=args.vehicle_id,
            fueling_type=args.type,
            fuelsortid=args.fuelsort,
            quantityunitid=args.unit
        )
        print("Successfully synced refueling to Spritmonitor!")
    except Exception as e:
        logger.error(f"Sync failed: {e}")
        exit(1)
