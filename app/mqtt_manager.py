import logging
import json
import time
from typing import Any, Dict, Optional
import paho.mqtt.client as mqtt  # type: ignore
from config import GshareConfig  # type: ignore
from datetime import datetime
import pytz  # type: ignore

class MQTTManager:
    def __init__(self, config: GshareConfig):
        self.config = config
        self.client: Optional[mqtt.Client] = None
        self.connected = False
        self._setup_client()

    def _setup_client(self):
        if not self.config.MQTT_BROKER:
            logging.info("MQTT 브로커 설정이 없어 MQTT 기능을 비활성화합니다.")
            return

        try:
            # Client ID 생성 (랜덤)
            client_id = f"gshare_manager_{int(time.time())}"
            self.client = mqtt.Client(client_id=client_id)

            if self.config.MQTT_USERNAME and self.config.MQTT_PASSWORD:
                self.client.username_pw_set(self.config.MQTT_USERNAME, self.config.MQTT_PASSWORD)

            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect

            # 비동기 연결 시작
            logging.info(f"MQTT 브로커 연결 시도: {self.config.MQTT_BROKER}:{self.config.MQTT_PORT}")
            self.client.connect_async(self.config.MQTT_BROKER, self.config.MQTT_PORT, 60)
            self.client.loop_start()

        except Exception as e:
            logging.error(f"MQTT 클라이언트 설정 중 오류: {e}")
            self.client = None

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logging.info("MQTT 브로커에 연결되었습니다.")
            self.connected = True
            self.publish_discovery()

            # Availability 토픽에 online 메시지 전송 (Retained)
            availability_topic = f"{self.config.MQTT_TOPIC_PREFIX}/status"
            self.client.publish(availability_topic, "online", retain=True)
        else:
            logging.error(f"MQTT 연결 실패, 결과 코드: {rc}")
            self.connected = False

    def _on_disconnect(self, client, userdata, rc):
        logging.warning("MQTT 브로커 연결이 끊어졌습니다.")
        self.connected = False

    def publish_state(self, state: Any):
        """현재 상태를 MQTT로 발행"""
        if not self.client or not self.connected:
            return

        try:
            topic = f"{self.config.MQTT_TOPIC_PREFIX}/state"

            # State 객체를 딕셔너리로 변환
            if hasattr(state, 'to_dict'):
                payload = state.to_dict()
            else:
                payload = state

            # monitored_folders는 너무 크거나 복잡할 수 있으므로 개수만 포함하거나 별도 처리
            if 'monitored_folders' in payload and isinstance(payload['monitored_folders'], dict):
                payload['watched_folder_count'] = len(payload['monitored_folders'])
                # 상세 정보는 제거하거나 별도 토픽으로 발행 고려 (여기서는 간단히 제거하지 않음)

            self.client.publish(topic, json.dumps(payload, default=str))
            # logging.debug(f"MQTT 상태 발행 완료: {topic}")

        except Exception as e:
            logging.error(f"MQTT 상태 발행 중 오류: {e}")

    def publish_discovery(self):
        """Home Assistant Discovery 메시지 발행"""
        if not self.client or not self.connected:
            return

        try:
            discovery_prefix = self.config.HA_DISCOVERY_PREFIX
            device_info = {
                "identifiers": ["gshare_manager"],
                "name": "GShare Manager",
                "manufacturer": "wooooooooooook",
                "model": "GShare Manager Docker",
                "sw_version": "1.0.0"
            }

            base_topic = f"{self.config.MQTT_TOPIC_PREFIX}/state"
            availability_topic = f"{self.config.MQTT_TOPIC_PREFIX}/status"

            # 센서 정의
            sensors = [
                {
                    "name": "VM Status",
                    "id": "vm_status",
                    "type": "binary_sensor",
                    "device_class": "running",
                    "value_template": "{{ 'ON' if value_json.vm_running else 'OFF' }}",
                    "icon": "mdi:server"
                },
                {
                    "name": "SMB Status",
                    "id": "smb_status",
                    "type": "binary_sensor",
                    "device_class": "connectivity",
                    "value_template": "{{ 'ON' if value_json.smb_running else 'OFF' }}",
                    "icon": "mdi:folder-network"
                },
                {
                    "name": "NFS Status",
                    "id": "nfs_status",
                    "type": "binary_sensor",
                    "device_class": "connectivity",
                    "value_template": "{{ 'ON' if value_json.nfs_mounted else 'OFF' }}",
                    "icon": "mdi:nas"
                },
                {
                    "name": "CPU Usage",
                    "id": "cpu_usage",
                    "type": "sensor",
                    "unit_of_measurement": "%",
                    "value_template": "{{ value_json.cpu_usage }}",
                    "icon": "mdi:cpu-64-bit"
                },
                {
                    "name": "Watched Folders Count",
                    "id": "watched_folders_count",
                    "type": "sensor",
                    "value_template": "{{ value_json.watched_folder_count }}",
                    "icon": "mdi:folder-multiple"
                },
                {
                    "name": "Last Check Time",
                    "id": "last_check_time",
                    "type": "sensor",
                    "device_class": "timestamp",
                    "value_template": "{{ value_json.last_check_time }}", # ISO 포맷 필요할 수 있음
                    "icon": "mdi:clock-check"
                },
                {
                    "name": "Last Action",
                    "id": "last_action",
                    "type": "sensor",
                    "value_template": "{{ value_json.last_action }}",
                    "icon": "mdi:history"
                },
                {
                    "name": "Last Shutdown Time",
                    "id": "last_shutdown_time",
                    "type": "sensor",
                    "device_class": "timestamp",
                    "value_template": "{{ value_json.last_shutdown_time }}",
                    "icon": "mdi:clock-end"
                }
            ]

            for sensor in sensors:
                component = sensor["type"]
                object_id = f"gshare_{sensor['id']}"
                discovery_topic = f"{discovery_prefix}/{component}/gshare/{sensor['id']}/config"

                payload = {
                    "name": f"GShare {sensor['name']}",
                    "unique_id": object_id,
                    "state_topic": base_topic,
                    "availability_topic": availability_topic,
                    "payload_available": "online",
                    "payload_not_available": "offline",
                    "device": device_info,
                    "value_template": sensor["value_template"]
                }

                if "device_class" in sensor:
                    payload["device_class"] = sensor["device_class"]
                if "unit_of_measurement" in sensor:
                    payload["unit_of_measurement"] = sensor["unit_of_measurement"]
                if "icon" in sensor:
                    payload["icon"] = sensor["icon"]

                self.client.publish(discovery_topic, json.dumps(payload), retain=True)

            logging.info("Home Assistant Discovery 메시지 발행 완료")

        except Exception as e:
            logging.error(f"Discovery 메시지 발행 중 오류: {e}")

    def disconnect(self):
        if self.client:
            # 종료 시 offline 메시지 전송
            try:
                if self.connected:
                    availability_topic = f"{self.config.MQTT_TOPIC_PREFIX}/status"
                    self.client.publish(availability_topic, "offline", retain=True)
            except:
                pass

            self.client.loop_stop()
            self.client.disconnect()
            logging.info("MQTT 연결 종료")
