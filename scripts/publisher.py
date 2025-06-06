#####################################################################
# This script reads the factory_state.json file generated by the    #
# sup-MQTT mod and publishes the data to an MQTT broker.            #
#                                                                   #
# The project is originally written by Mario Gonsales Ishikawa.     #
# Modified by lvhsrd.                                               #
# License: Apache License 2.0                                       #
#                                                                   #
# The script will publish the following data:                       #
# - Asset IDs for each category/type                                #
# - Asset data for each asset                                       #
#                                                                   #
# To leverage this script, you need to have a running MQTT          #
# broker to which the data will be published. Rename                #
# config.toml.example to config.toml and fill in the values to use. #
#####################################################################
import time
import os
import json
import toml
from paho.mqtt import client as mqtt_client

try:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, "config.toml")
    config = toml.load(config_path)
except Exception as e:
    print(f"Error loading config.toml: {e}")
    exit(1)

# Get values from config.toml
FACTORY_STATE_FILE = os.path.expanduser(config['paths']['factory_state_file'])
BROKER = config['mqtt']['broker']
PORT = config['mqtt']['port']
TOPIC_PREFIX = config['mqtt']['topic_prefix']
ADMIN = config['mqtt']['username']
PASSWORD = config['mqtt']['password']
LOG_FILE = os.path.join(script_dir, config['paths']['log_file'])

# Keep track of last published values for each subtopic, so we only publish if changed
last_published = {}

def publish_if_changed(client, subtopic, new_payload,log_file):
    """
    Publish 'new_payload' to 'subtopic' only if 'new_payload' differs 
    from the last published payload for this subtopic.
    """
    old_payload = last_published.get(subtopic)
    if old_payload != new_payload:
        client.publish(subtopic, new_payload)
        last_published[subtopic] = new_payload
        log_file.write(f"{subtopic}: {new_payload}\n")

def publish_no_matter(client, subtopic, payload,log_file):
    """
    Publish 'payload' to 'subtopic' unconditionally.
    """
    client.publish(subtopic, payload)
    log_file.write(f"{subtopic}: {payload}\n")

def publish_json(client, subtopic, value,log_file):
    """
    Convert 'value' to a valid JSON string via json.dumps(...),
    then publish if changed.
    """
    # always a valid JSON representation
    payload = json.dumps(value) 
    #publish_if_changed is mainly used unless u want to test, then you can use publish_no_matter
    publish_if_changed(client, subtopic, payload,log_file) 
    # publish_no_matter(client, subtopic, payload,log_file)

# 1) Category map
TYPE_TO_CATEGORY = {
    "assembling-machine": "Assembly",
    "furnace":            "Smelting",
    "mining-drill":       "Mining",
    "boiler":             "Power",
    "pump":               "Power",
    "generator":          "Power",
    "electric-pole":      "Power",
    "container":          "Storage",
    "logistic-container": "Storage",
    "car":                "Transport",
    "cargo-wagon":        "Transport",
    "fluid-wagon":        "Transport",
    "locomotive":         "Transport",
    "spider-vehicle":     "Transport",
    "roboport":           "Logistics",
}


# 2) Factorio 2.0 bitmask status definitions
STATUS_FLAGS = {
    1:    "working",
    2:    "no_power",
    4:    "no_fuel",
    8:    "low_power",
    16:   "no_minable_resources",
    32:   "disabled_by_control_behavior",
    64:   "disabled_by_script",
    128:  "item_ingredient_shortage",
    256:  "fluid_ingredient_shortage",
    512:  "full_output",
    1024: "no_research_in_progress",
}
STATUS_PRIORITY = [
    (1024, "No Research In Progress"),
    (512,  "Output Full"),
    (8,    "Low Power"),
    (64,   "Disabled by Script"),
    (32,   "Target Full"),
    (256,  "Fluid Shortage"),
    (128,  "Item Shortage"),
    (16,   "No Resources"),
    (2,    "No Power"),
    (4,    "No Fuel"),
    (1,    "Working")
]


def decode_all_flags(status_int):
    flags = []
    for bit_value, name in STATUS_FLAGS.items():
        if (status_int & bit_value) != 0:
            flags.append(name)
    if not flags:
        flags.append("none")
    return flags

def decode_dominant_flag(status_int):
    for bit_val, label in STATUS_PRIORITY:
        if (status_int & bit_val) != 0:
            return label
    return "none"

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        print("Connected to MQTT Broker successfully!")
    else:
        print(f"Failed to connect, return code {reason_code}")

def connect_mqtt():
    client = mqtt_client.Client()
    try:
        if ADMIN and PASSWORD:
            client.username_pw_set(ADMIN, PASSWORD)
        client.on_connect = on_connect
        client.connect(BROKER, PORT, keepalive=60)
        print("Connecting to MQTT broker...")
        return client
    except Exception as e:
        print(f"Error connecting to MQTT broker: {e}")
        exit(1)

def publish_asset_list(client, asset_groups,log_file):
    """
    For each (category, type_slug), publish the array of IDs (as JSON).
    E.g. factorio/Mining/line_123/mining_drill => '[{"id":14},{"id":20}]'
    """
    for (category,line_id, type_slug), id_list in asset_groups.items():
        topic = f"{TOPIC_PREFIX}/{category}/{line_id}/{type_slug}"
        # 'id_list' is a Python list like: [ {"id":14}, {"id":20} ]
        publish_json(client, topic, id_list,log_file)

def publish_asset_data(client, asset,log_file):
    """
    Publish data for a single asset to multiple subtopics:
      factorio/<category>/<type_slug>/<id>/<subtopic>
    All values are JSON-encoded for correct parsing.

    'unit_number' -> 'id'
    """
    asset_id = asset.get("id", asset.get("unit_number", "unknown_id"))
    asset["id"] = asset_id

    asset_type = asset.get("type", "unknown")
    category   = TYPE_TO_CATEGORY.get(asset_type, "other")
    type_slug  = asset_type.replace('-', '')
    if type_slug == "assemblingmachine": # Special for assembling-machine since its too long
        type_slug = "assem"
    line_id = asset.get("line_id", "Isolated")  # Get Line ID，default "Isolated"
    type_plus_id = type_slug + str(asset_id)

    base_topic = f"{TOPIC_PREFIX}/{category}/{line_id}/{type_plus_id}"

    # 1) Basic info
    basic_info = {
        "id": str(asset_id),
        "name": asset.get("name", "unknown_name"),
        "type": asset_type,
    }
    publish_json(client, f"{base_topic}/basic", basic_info, log_file)

    publish_json(client, f"{base_topic}/pos", asset.get("position", {}),log_file)

    # 2) Status
    raw_status = asset.get("last_status", 0)
    dominant   = decode_dominant_flag(raw_status)
    all_flags  = decode_all_flags(raw_status)
    all_flags_str = ', '.join(all_flags)
    # state_changed_tick
    state_changed_tick = asset.get("state_changed_tick", 0)

    status_info = {
        "status": dominant,
        "statusBitmask": raw_status,
        "allFlags": all_flags_str,
        "stateChangedTick" : state_changed_tick
    }
    publish_json(client, f"{base_topic}/status", status_info,log_file)

    # 3) Production
    production_count       = asset.get("production_count", 0)
    production_last_update = asset.get("production_last_updated", 0)
    production_info ={
        "count": production_count,
        "lastUpdated": production_last_update
    }
    publish_json(client, f"{base_topic}/production", production_info , log_file)

    # 4) Pollution (Time series data)
    pollution_value = asset.get("pollution", 0.0)
    pollution_info={
        "pollution": pollution_value
    }
    publish_json(client, f"{base_topic}/pollution", pollution_info, log_file)

    # 5) Inventory can be either a dict or a list, so handle both cases
    inventory = asset.get("inventory", {})
    inventory_topic_prefix = f"{base_topic}/inventory"
    if isinstance(inventory, dict):
        for inv_label, stack_list in inventory.items():
            publish_json(client, f"{inventory_topic_prefix}/{inv_label}", stack_list,log_file)
    elif isinstance(inventory, list):
        publish_json(client, f"{inventory_topic_prefix}", inventory,log_file)
    else:
        publish_json(client, f"{inventory_topic_prefix}", str(inventory),log_file)

    # 6) Fluids
    fluids = asset.get("fluids", [])
    # If you want each fluid box in a single array, you can do:
    # publish_json(client, f"{base_topic}/fluids", fluids)
    # Or individually:
    for i, fluid in enumerate(fluids):
        fluid_topic = f"{base_topic}/fluids/box{i}"
        publish_json(client, fluid_topic, fluid if fluid else "empty",log_file)

    # 7) NEW: Electrical
    electric_info = asset.get("electric",{})
    publish_json(client, f"{base_topic}/electricity", electric_info, log_file)

# Create a temp log file object, used to collect all the topic from this time
class TopicCollector:
    def __init__(self):
        self.topics = {}
    
    def write(self, line):
        # Decode lines, extract topic and payload
        if ": " in line:
            parts = line.split(": ", 1)
            if len(parts) == 2:
                topic, payload = parts
                self.topics[topic] = payload

def main():
    client = connect_mqtt()
    client.loop_start()

    last_mtime = 0

    while True:
        time.sleep(2)
        if not os.path.exists(FACTORY_STATE_FILE):
            print(f"Error: {FACTORY_STATE_FILE} does not exist")
            continue
        mtime = os.path.getmtime(FACTORY_STATE_FILE)
        if mtime > last_mtime:
            last_mtime = mtime
            # file changed, read new snapshot
            try:
                with open(FACTORY_STATE_FILE, "r") as f:
                    data = json.load(f)

                # data: {"tick": ..., "assets": [...]}
                assets = data.get("assets", [])
                if not isinstance(assets, list):
                    print("Error: data['assets'] is not a list.")
                    continue

                # Open log file for writing (overwrite previous content)
                with open(LOG_FILE, "w") as log_file:
                    # 1) Build dict grouping by (category, type_slug) -> list of { "id": ... }
                    asset_groups = {}
                    for asset in assets:
                        a_type     = asset.get("type", "unknown")
                        category   = TYPE_TO_CATEGORY.get(a_type, "other")
                        line_id = asset.get("line_id", "Isolated")  # get Line ID
                        type_slug  = a_type.replace('-', '_')
                        asset_id   = asset.get("id", asset.get("unit_number", "unknown_id"))

                        key = (category, line_id, type_slug)
                        if key not in asset_groups:
                            asset_groups[key] = []
                        asset_groups[key].append({"id": asset_id})
                    
                        # Publish subtopics for each asset
                        for asset in assets:
                            publish_asset_data(client, asset,log_file)

            except Exception as e:
                print("Error parsing factory_state.json:", e)

if __name__ == "__main__":
    main()
