import os
import re
import time
from urllib.parse import urlencode

import requests
import urllib3
from loguru import logger

from bambu_spoolman.settings import get_http_timeout, get_rfid_field_key

NATIVE_TAGS_MIN_VERSION = (0, 27, 0)
BAMBU_PHYSICAL_TAG_FORMAT = "bambu"
BAMBU_TRAY_TAG_FORMAT = "bambu-tray"
BAMBU_TAG_FORMATS = frozenset({BAMBU_PHYSICAL_TAG_FORMAT, BAMBU_TRAY_TAG_FORMAT})


class SpoolmanClient:
    """
    A client for the Spoolman API
    """

    def __init__(self, endpoint):
        if not endpoint or not str(endpoint).strip():
            raise ValueError("Spoolman endpoint must be configured")
        self.endpoint = str(endpoint).strip().rstrip("/")
        self.verify = os.environ.get("SPOOLMAN_VERIFY", "true").lower() == "true"
        self.timeout = get_http_timeout()
        self._external_filaments_cache = None
        self._external_filaments_cache_time = None
        self._supports_native_tags = None
        self.ams_field_name = os.environ.get("SPOOLMAN_AMS_FIELD_NAME")
        self.tray_field_name = os.environ.get("SPOOLMAN_TRAY_FIELD_NAME")

        if not self.verify:
            urllib3.disable_warnings()

    def validate(self):
        """
        Validates the connection to the Spoolman API
        """
        try:
            response = requests.get(
                self._make_api_route("health"),
                verify=self.verify,
                timeout=self.timeout,
            )
            return response.status_code == 200
        except requests.RequestException as e:
            logger.warning("Could not validate Spoolman connection: {}", e)
            return False

    def get_info(self):
        """
        Get information about the Spoolman instance
        """
        response = requests.get(
            self._make_api_route("info"),
            verify=self.verify,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def get_filaments(self):
        """
        Get a list of all filaments
        """
        response = requests.get(
            self._make_api_route("filament"),
            verify=self.verify,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def get_spools(self):
        """
        Get a list of all spools
        """
        response = requests.get(
            self._make_api_route("spool"),
            verify=self.verify,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def get_external_filaments(self, use_cache=True):
        """
        Get a list of all external filaments from SpoolmanDB
        Caches the result for 1 hour to avoid repeated large fetches
        Set use_cache=False to force a fresh fetch
        """
        # Check cache (1 hour = 3600 seconds)
        cache_ttl = 3600
        if use_cache and self._external_filaments_cache is not None:
            if self._external_filaments_cache_time is not None:
                age = time.time() - self._external_filaments_cache_time
                if age < cache_ttl:
                    logger.debug(f"Using cached external filaments ({int(age)}s old)")
                    return self._external_filaments_cache

        # Fetch fresh data
        try:
            logger.info("Fetching external filaments from SpoolmanDB...")
            response = requests.get(
                self._make_api_route("external/filament"),
                verify=self.verify,
                timeout=self.timeout,
            )
            response.raise_for_status()

            data = response.json()
            # Update cache
            self._external_filaments_cache = data
            self._external_filaments_cache_time = time.time()
            logger.info(f"Cached {len(data)} external filaments")
            return data
        except Exception as e:
            logger.error(f"Exception getting external filaments: {e}")
            return (
                self._external_filaments_cache or []
            )  # Return stale cache if available

    def get_spool(self, spool_id):
        """
        Get a specific spool by ID
        """
        try:
            response = requests.get(
                self._make_api_route(f"spool/{spool_id}"),
                verify=self.verify,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError:
            return None

    def supports_native_tags(self):
        """Return whether the connected Spoolman has the v0.27 tag API."""
        if self._supports_native_tags is not None:
            return self._supports_native_tags

        try:
            version = str(self.get_info().get("version", ""))
        except (requests.RequestException, AttributeError, TypeError, ValueError) as e:
            # Do not cache connection failures. Spoolman may still be starting, and a
            # later MQTT update or UI request should be able to discover the feature.
            logger.warning("Could not detect Spoolman native tag support: {}", e)
            return False

        match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", version)
        self._supports_native_tags = bool(
            match
            and tuple(int(part) for part in match.groups()) >= NATIVE_TAGS_MIN_VERSION
        )
        return self._supports_native_tags

    def scan_tag(self, tag_uid, *, reader_id=None, name=None):
        """Report a physical tag read and return the matched spool, if any."""
        if not tag_uid or not self.supports_native_tags():
            return None

        payload = {"uid": tag_uid, "format": BAMBU_PHYSICAL_TAG_FORMAT}
        if reader_id:
            payload["reader_id"] = reader_id
        if name:
            payload["name"] = name

        try:
            response = requests.post(
                self._make_api_route("tag/scan"),
                json=payload,
                verify=self.verify,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json().get("spool")
        except requests.RequestException as e:
            logger.warning("Failed to report Bambu RFID scan {}: {}", tag_uid, e)
            return None

    def find_spool_by_tag(self, tag_uid, *, allow_archived=False):
        """Find one spool by a native Spoolman tag UID."""
        if not tag_uid or not self.supports_native_tags():
            return None

        try:
            response = requests.get(
                self._make_api_route(
                    "spool",
                    tag=tag_uid,
                    allow_archived=str(allow_archived).lower(),
                ),
                verify=self.verify,
                timeout=self.timeout,
            )
            response.raise_for_status()
            spools = response.json()
            return spools[0] if spools else None
        except requests.RequestException as e:
            logger.warning("Failed to look up Spoolman tag {}: {}", tag_uid, e)
            return None

    def link_tag(self, spool_id, tag_uid, tag_format):
        """Idempotently link a native Spoolman tag to a spool."""
        if not tag_uid or not self.supports_native_tags():
            return False

        try:
            response = requests.post(
                self._make_api_route(f"spool/{spool_id}/tag"),
                json={"uid": tag_uid, "format": tag_format},
                verify=self.verify,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return True
        except requests.RequestException as e:
            status = getattr(e.response, "status_code", None)
            if status == 409:
                logger.warning(
                    "Bambu RFID tag {} is already linked to another Spoolman item",
                    tag_uid,
                )
            else:
                logger.warning(
                    "Failed to link Bambu RFID tag {} to spool {}: {}",
                    tag_uid,
                    spool_id,
                    e,
                )
            return False

    def link_bambu_tags(self, spool_id, *, tray_uuid=None, tag_uid=None):
        """Link Bambu's stable spool identity and currently visible physical tag."""
        if not self.supports_native_tags():
            return False

        results = []
        if tray_uuid:
            results.append(self.link_tag(spool_id, tray_uuid, BAMBU_TRAY_TAG_FORMAT))
        if tag_uid and not _is_zero_identifier(tag_uid):
            results.append(self.link_tag(spool_id, tag_uid, BAMBU_PHYSICAL_TAG_FORMAT))
        return bool(results) and all(results)

    def unlink_bambu_tags(self, spool_id, spool=None):
        """Remove only native tags managed by this integration."""
        if not self.supports_native_tags():
            return False

        spool = spool or self.get_spool(spool_id)
        if spool is None:
            return False

        managed_tags = [
            tag
            for tag in spool.get("tags", [])
            if tag.get("format") in BAMBU_TAG_FORMATS and tag.get("uid")
        ]
        success = True
        for tag in managed_tags:
            try:
                response = requests.delete(
                    self._make_api_route(f"spool/{spool_id}/tag/{tag['uid']}"),
                    verify=self.verify,
                    timeout=self.timeout,
                )
                response.raise_for_status()
            except requests.RequestException as e:
                success = False
                logger.warning(
                    "Failed to unlink Bambu RFID tag {} from spool {}: {}",
                    tag["uid"],
                    spool_id,
                    e,
                )
        return success

    def consume_spool(self, spool_id, *, length=None, weight=None):
        """
        Consume a part of a spool
        """
        if (length is None) == (weight is None):
            raise ValueError("Must provide exactly one of length or weight")

        amount = length if length is not None else weight
        if amount <= 0:
            raise ValueError("Consumption amount must be positive")

        response = requests.put(
            self._make_api_route(f"spool/{spool_id}/use"),
            json={
                "use_length": length,
                "use_weight": weight,
            },
            verify=self.verify,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def lookup_by_tray_uuid(self, tray_uuid):
        """
        Looks up a spoolman spool by the tray uuid
        """
        if not tray_uuid:
            return None
        native_match = self.find_spool_by_tag(tray_uuid)
        if native_match is not None:
            return native_match

        extra_field = get_rfid_field_key()
        if extra_field is None:
            return None
        all_spools = self.get_spools()

        for spool in all_spools:
            extra = spool.get("extra", {})

            data = extra.get(extra_field, None)
            if data is not None and data == f'"{tray_uuid}"':
                return spool
        return None

    def set_tray_uuid(self, spool_id, tray_uuid):
        """
        Sets a tray's uuid
        """
        existing_spool = self.get_spool(spool_id)
        if existing_spool is None:
            return False

        native_supported = self.supports_native_tags()
        native_success = False
        if tray_uuid:
            native_success = self.link_bambu_tags(spool_id, tray_uuid=tray_uuid)
            if native_supported and not native_success:
                return False
        elif native_supported:
            native_success = self.unlink_bambu_tags(spool_id, existing_spool)

        extra_field = get_rfid_field_key()
        if extra_field is None:
            return native_success
        # Get extra data
        extra = existing_spool.get("extra", {})
        # An empty UUID permanently removes the RFID association.
        if tray_uuid:
            extra[extra_field] = f'"{tray_uuid}"'
        else:
            extra.pop(extra_field, None)
        # Update the spool
        try:
            response = requests.patch(
                self._make_api_route(f"spool/{spool_id}"),
                json={"extra": extra},
                verify=self.verify,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return native_success if native_supported else True
        except requests.exceptions.RequestException:
            return native_success

    def supports_tray_locking(self):
        return self.supports_native_tags() or get_rfid_field_key() is not None

    def set_active_tray(self, spool_id, ams_num=None, tray_num=None):
        """
        Sets the AMS and tray fields for a spool

        Args:
            spool_id: The spool ID to update
            ams_num: The AMS number (1-indexed), or None to clear
            tray_num: The tray number (1-indexed), or None to clear

        Uses environment variables:
            SPOOLMAN_AMS_FIELD_NAME: Field name for AMS number (e.g., "ams_num")
            SPOOLMAN_TRAY_FIELD_NAME: Field name for tray number (e.g., "ams_tray")

        Returns False if neither environment variable is set
        """
        # Skip if neither environment variable is set
        if self.ams_field_name is None and self.tray_field_name is None:
            logger.debug(
                "Neither SPOOLMAN_AMS_FIELD_NAME nor SPOOLMAN_TRAY_FIELD_NAME "
                "is set; skipping tray field update"
            )
            return False

        existing_spool = self.get_spool(spool_id)
        if existing_spool is None:
            logger.warning(f"Spool {spool_id} not found, cannot set tray fields")
            return False

        # Get extra data
        extra = existing_spool.get("extra", {})

        # Set or clear the AMS field
        if self.ams_field_name:
            if ams_num is not None:
                extra[self.ams_field_name] = f'"{ams_num}"'
            else:
                extra[self.ams_field_name] = '""'

        # Set or clear the tray field
        if self.tray_field_name:
            if tray_num is not None:
                extra[self.tray_field_name] = f'"{tray_num}"'
            else:
                extra[self.tray_field_name] = '""'

        # Update the spool
        try:
            response = requests.patch(
                self._make_api_route(f"spool/{spool_id}"),
                json={"extra": extra},
                verify=self.verify,
                timeout=self.timeout,
            )
            response.raise_for_status()
            logger.debug(
                "Set AMS/tray fields for spool {}: AMS={}, Tray={}",
                spool_id,
                ams_num,
                tray_num,
            )
            return True
        except requests.exceptions.RequestException as e:
            logger.error("Failed to set AMS/tray fields for spool {}: {}", spool_id, e)
            return False

    def create_spool(
        self, filament_id, tray_uuid, initial_weight=1000, *, tag_uid=None
    ):
        """
        Creates a new spool in Spoolman
        Returns the created spool or None on failure
        """
        extra_field = get_rfid_field_key()
        extra = {}
        if extra_field:
            extra[extra_field] = f'"{tray_uuid}"'

        spool_data = {
            "filament_id": filament_id,
            "initial_weight": initial_weight,
            "remaining_weight": initial_weight,
            "spool_weight": 250,  # Bambu Lab default
            "extra": extra,
        }

        try:
            response = requests.post(
                self._make_api_route("spool"),
                json=spool_data,
                verify=self.verify,
                timeout=self.timeout,
            )

            response.raise_for_status()

            logger.info(f"Created spool with filament_id {filament_id}")

            created_spool = response.json()
            self.link_bambu_tags(
                created_spool["id"], tray_uuid=tray_uuid, tag_uid=tag_uid
            )
            return created_spool
        except Exception as e:
            logger.error(f"Exception creating spool: {e}")
            return None

    def match_external_filament(self, tray_data):
        """
        Finds a matching external filament using a refined matching algorithm
        Steps:
        1. Filter by Bambu Lab manufacturer/vendor
        2. Filter by exact color hex match
        3. Try to match by sub_brand with various separators (underscore, plus, hyphen)
        4. Fall back to material type matching
        Returns the best match or None
        """
        external_filaments = self.get_external_filaments()
        if not external_filaments:
            logger.debug("No external filaments available")
            return None

        filament_type = tray_data.get("tray_type", "")
        filament_sub_brand = tray_data.get("tray_sub_brands", "")

        # Extract colors from cols array (supports multi-color filaments)
        cols = tray_data.get("cols", [])
        if cols:
            # Use cols array if available
            color_hexes = [col[:6].upper() for col in cols]
        else:
            # Fall back to tray_color for backwards compatibility
            tray_color = tray_data.get("tray_color", "")
            color_hexes = [tray_color[:6].upper()] if tray_color else []

        # Step 1: Filter by Bambu Lab manufacturer/vendor
        bambu_filaments = []
        for filament in external_filaments:
            manufacturer = filament.get("manufacturer", "")
            # Handle both dict and string manufacturer
            if isinstance(manufacturer, dict):
                manufacturer_name = manufacturer.get("name", "")
            elif isinstance(manufacturer, str):
                manufacturer_name = manufacturer
            else:
                manufacturer_name = ""

            if "bambu" in manufacturer_name.lower():
                bambu_filaments.append(filament)

        if not bambu_filaments:
            logger.debug("No Bambu Lab filaments found in external database")
            return None

        # Step 2: Filter by color (using intersection - any color match)
        color_matched = []
        for filament in bambu_filaments:
            # Handle both color_hex (single) and color_hexes (multiple)
            filament_colors = filament.get("color_hex") or filament.get("color_hexes")

            # Convert to list if single value
            if isinstance(filament_colors, str):
                filament_colors = [filament_colors]
            elif filament_colors is None:
                filament_colors = []

            filament_colors = [
                color.lstrip("#")[:6].upper()
                for color in filament_colors
                if isinstance(color, str) and color
            ]

            # Match when the tray and external filament color sets intersect.
            if any(color in filament_colors for color in color_hexes):
                color_matched.append(filament)

        if not color_matched:
            logger.debug(f"No color match found for {color_hexes}")
            return None

        # Step 3: Try to match by sub_brand/specific name (e.g., "PETG HF")
        if filament_sub_brand and filament_sub_brand.strip():
            sub_brands = [
                filament_sub_brand.replace(" ", "_").lower(),
                filament_sub_brand.replace(" ", "+").lower(),
                filament_sub_brand.replace(" ", "-").lower(),
            ]

            for filament in color_matched:
                filament_id = filament.get("id", "").lower()
                for brand in sub_brands:
                    if brand in filament_id:
                        logger.info(
                            "Found exact sub-brand match: {} (id: {})",
                            filament.get("name"),
                            filament_id,
                        )
                        return filament

        # Step 4: Filter by material type (fallback)
        for filament in color_matched:
            filament_material = filament.get("material", "").upper()
            if filament_material == filament_type.upper():
                logger.info(
                    "Found material type match: {} (id: {})",
                    filament.get("name"),
                    filament.get("id"),
                )
                return filament

        logger.debug(
            "No external filament match for {} ({}) with color {}",
            filament_sub_brand,
            filament_type,
            color_hexes,
        )
        return None

    def create_filament_from_external(self, external_filament, tray_material=None):
        """
        Creates a filament from an external filament definition
        tray_material: Optional tray material used to preserve the full variant
        Returns the created filament or None on failure
        """
        try:
            # Get or create the vendor
            manufacturer = external_filament.get("manufacturer", {})
            # Handle both dict and string manufacturer
            if isinstance(manufacturer, dict):
                vendor_name = manufacturer.get("name", "Unknown")
            elif isinstance(manufacturer, str):
                vendor_name = manufacturer
            else:
                vendor_name = "Unknown"

            vendor = self._get_or_create_vendor(vendor_name)
            if vendor is None:
                logger.error(f"Failed to get or create vendor: {vendor_name}")
                return None

            # Use tray material to preserve variants such as Matte and Basic.
            # Otherwise fall back to external filament's material
            material = (
                tray_material
                if tray_material
                else external_filament.get("material", "PLA")
            )

            # Handle multi-color filaments
            # External records use color_hex (single) or color_hexes (multiple).
            color_hex = external_filament.get("color_hex")
            color_hexes = external_filament.get("color_hexes")

            filament_data = {
                "name": external_filament.get("name", "Unknown"),
                "material": material,
                "vendor_id": vendor["id"],
                "diameter": external_filament.get("diameter", 1.75),
                "weight": external_filament.get("weight", 1000),
                "density": external_filament.get("density", 1.24),
                "spool_weight": external_filament.get("spool_weight", 250),
                "external_id": external_filament.get("id"),
            }

            # Set color fields based on what's available
            if color_hexes:
                # Multi-color filament
                filament_data["multi_color_hexes"] = ",".join(color_hexes)
                filament_data["multi_color_direction"] = external_filament.get(
                    "multi_color_direction", "coaxial"
                )
            elif color_hex:
                # Single color filament
                filament_data["color_hex"] = color_hex
            else:
                # Default to black if no color specified
                filament_data["color_hex"] = "000000"

            response = requests.post(
                self._make_api_route("filament"),
                json=filament_data,
                verify=self.verify,
                timeout=self.timeout,
            )
            response.raise_for_status()

            logger.info(f"Created filament from external: {filament_data['name']}")
            return response.json()
        except requests.exceptions.HTTPError as e:
            logger.error(
                "HTTP error creating filament from external: status={}, response={}",
                e.response.status_code,
                e.response.text,
            )
            return None
        except Exception as e:
            logger.error(f"Exception creating filament from external: {e}")
            return None

    def auto_create_spool_from_tray(self, tray_data):
        """
        Automatically creates a spool from tray data
        First tries to match with external filaments, then falls back to basic creation
        tray_data contains the RFID, material, color, and weight reported by the tray
        Returns the created spool or None on failure
        """
        tray_uuid = tray_data.get("tray_uuid")
        material = (
            tray_data.get("tray_sub_brands") or tray_data.get("tray_type") or "PLA"
        )
        color_hex = tray_data.get("tray_color") or "000000"
        tray_weight = tray_data.get("tray_weight")

        # Clean up color hex (remove alpha channel if present)
        if len(color_hex) == 8:
            color_hex = color_hex[:6]

        try:
            weight_int = int(tray_weight) if tray_weight else 1000
        except TypeError, ValueError:
            weight_int = 1000

        logger.info(f"Auto-creating spool: {material} {color_hex} ({weight_int}g)")

        # Try to find matching external filament first
        external_filament = self.match_external_filament(tray_data)

        filament = None
        if external_filament:
            # Preserve the material variant reported by the tray.
            filament = self.create_filament_from_external(external_filament, material)

        if filament is None:
            logger.info("Auto-creating spool failed due to no found filament")
            return None

        # Create the spool with the tray UUID
        spool = self.create_spool(
            filament["id"],
            tray_uuid,
            weight_int,
            tag_uid=tray_data.get("tag_uid"),
        )
        return spool

    def _get_or_create_vendor(self, vendor_name):
        """
        Gets a vendor by name or creates it if it doesn't exist
        """
        try:
            # Try to find existing vendor
            response = requests.get(
                self._make_api_route("vendor"),
                verify=self.verify,
                timeout=self.timeout,
            )
            response.raise_for_status()
            vendors = response.json()
            for vendor in vendors:
                if vendor.get("name") == vendor_name:
                    return vendor

            # Vendor not found, create it
            vendor_data = {
                "name": vendor_name,
                "empty_spool_weight": 250,
            }
            response = requests.post(
                self._make_api_route("vendor"),
                json=vendor_data,
                verify=self.verify,
                timeout=self.timeout,
            )
            response.raise_for_status()

            logger.info(f"Created vendor: {vendor_name}")
            return response.json()
        except Exception as e:
            logger.error(f"Exception in _get_or_create_vendor: {e}")
            return None

    def _make_api_route(self, route, **kwargs):
        query_string = urlencode(kwargs)
        if query_string:
            return f"{self.endpoint}/api/v1/{route}?{query_string}"
        return f"{self.endpoint}/api/v1/{route}"


def _is_zero_identifier(value):
    return bool(value) and set(str(value)) == {"0"}


def new_client(url=None) -> SpoolmanClient:
    """
    Create a new Spoolman client
    """
    if url is None:
        url = os.environ.get("SPOOLMAN_URL")
    return SpoolmanClient(url)


spoolman_client_instance = None


def instance():
    """
    Gets a singleton instance of the Spoolman client.
    """
    global spoolman_client_instance
    if spoolman_client_instance is None:
        spoolman_client_instance = new_client()
    return spoolman_client_instance
