import logging
import json
import time
import base64
from typing import Any, Dict, Optional, List, Callable
import paho.mqtt.client as mqtt  # type: ignore
from config import GshareConfig  # type: ignore

class MQTTManager:
    def __init__(self, config: GshareConfig, command_handler: Optional[Callable[[str, Optional[Dict[str, Any]]], None]] = None,
                 number_set_handler: Optional[Callable[[str, float], None]] = None):
        self.config = config
        self.client: Optional[mqtt.Client] = None
        self.connected = False
        self.command_handler = command_handler
        self.number_set_handler = number_set_handler
        self._setup_client()

    def set_command_handler(self, handler: Optional[Callable[[str, Optional[Dict[str, Any]]], None]]) -> None:
        self.command_handler = handler

    def set_number_set_handler(self, handler: Optional[Callable[[str, float], None]]) -> None:
        self.number_set_handler = handler

    def _setup_client(self):
        if not getattr(self.config, 'MQTT_ENABLED', True):
            logging.info("MQTT 기능이 비활성화되어 있습니다.")
            return

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
            self.client.on_message = self._on_message

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
            command_topic = f"{self.config.MQTT_TOPIC_PREFIX}/command"
            self.client.subscribe(command_topic, qos=0)
            # number 엔티티 set 토픽도 구독 (양방향: 값 변경 시 백엔드 메모리에 반영)
            number_set_topic = f"{self.config.MQTT_TOPIC_PREFIX}/recent_mount_days/set"
            self.client.subscribe(number_set_topic, qos=0)
        else:
            logging.error(f"MQTT 연결 실패, 결과 코드: {rc}")
            self.connected = False

    def _on_disconnect(self, client, userdata, rc):
        logging.warning("MQTT 브로커 연결이 끊어졌습니다.")
        self.connected = False

    def _on_message(self, client, userdata, msg):
        try:
            payload_raw = msg.payload.decode("utf-8").strip()

            # number 엔티티 set 토픽 처리: 숫자 페이로드를 number_set_handler로 위임
            if msg.topic == f"{self.config.MQTT_TOPIC_PREFIX}/recent_mount_days/set":
                try:
                    value = float(payload_raw)
                    # state 토픽에 retain 발행 (Home Assistant에 현재 값 반영)
                    state_topic = f"{self.config.MQTT_TOPIC_PREFIX}/recent_mount_days/state"
                    self.client.publish(state_topic, payload_raw, retain=True)
                    if self.number_set_handler:
                        self.number_set_handler("recent_mount_days", value)
                    else:
                        logging.warning("number_set_handler가 등록되지 않아 days 값을 메모리에 반영하지 않습니다.")
                except ValueError:
                    logging.warning(f"잘못된 number 페이로드: {payload_raw}")
                return

            data: Optional[Dict[str, Any]] = None
            command = payload_raw

            if payload_raw.startswith("{"):
                data = json.loads(payload_raw)
                command = str(data.get("command", "")).strip()

            if not command:
                logging.warning(f"MQTT 명령이 비어있습니다. topic={msg.topic}")
                return

            logging.info(f"MQTT 명령 수신: command={command}, topic={msg.topic}")

            if self.command_handler:
                self.command_handler(command, data)
            else:
                logging.warning("등록된 MQTT 명령 핸들러가 없어 명령을 무시합니다.")

        except Exception as e:
            logging.error(f"MQTT 명령 처리 실패: {e}")

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

            # recent_mount_days number 엔티티 state_topic에 retain 발행
            if 'recent_mount_days' in payload:
                rmd = int(payload['recent_mount_days'])
                self.client.publish(
                    f"{self.config.MQTT_TOPIC_PREFIX}/recent_mount_days/state",
                    str(rmd), retain=True)

        except Exception as e:
            logging.error(f"MQTT 상태 발행 중 오류: {e}")

    @property
    def _device_info(self) -> Dict[str, Any]:
        return {
            "identifiers": ["gshare_manager"],
            "name": "GShare Manager",
            "manufacturer": "wooooooooooook",
            "model": "GShare Manager Docker",
            "sw_version": "1.0.0"
        }

    @property
    def _sensor_definitions(self) -> List[Dict[str, Any]]:
        return [
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
            },
            {
                "name": "Latest Screenshot",
                "id": "latest_screenshot",
                "type": "camera",
                "topic": f"{self.config.MQTT_TOPIC_PREFIX}/camera/latest",
                "image_encoding": "b64",
                "icon": "mdi:camera"
            },
            {
                "name": "Recent Mount Days",
                "id": "recent_mount_days",
                "type": "number",
                "command_topic": f"{self.config.MQTT_TOPIC_PREFIX}/recent_mount_days/set",
                "state_topic": f"{self.config.MQTT_TOPIC_PREFIX}/recent_mount_days/state",
                "min": 1,
                "max": 3650,
                "step": 1,
                "initial": 3,
                "mode": "box",
                "unit_of_measurement": "days",
                "icon": "mdi:calendar-clock"
            },
            {
                "name": "Bulk Mount Recent",
                "id": "bulk_mount_recent",
                "type": "button",
                "command_topic": f"{self.config.MQTT_TOPIC_PREFIX}/command",
                "payload_press": "bulk_mount_recent",
                "icon": "mdi:folder-multiple-plus"
            },
            {
                "name": "Android VM On",
                "id": "android_vm_on",
                "type": "button",
                "command_topic": f"{self.config.MQTT_TOPIC_PREFIX}/command",
                "payload_press": "android_vm_on",
                "icon": "mdi:play"
            }
        ]

    def _build_discovery_payload(self, sensor: Dict[str, Any]) -> Dict[str, Any]:
        availability_topic = f"{self.config.MQTT_TOPIC_PREFIX}/status"
        object_id = f"gshare_{sensor['id']}"

        payload = {
            "name": f"GShare {sensor['name']}",
            "unique_id": object_id,
            "availability_topic": availability_topic,
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": self._device_info
        }

        if sensor["type"] == "button":
            payload["command_topic"] = sensor["command_topic"]
            payload["payload_press"] = sensor["payload_press"]
        elif sensor["type"] == "number":
            payload["command_topic"] = sensor["command_topic"]
            payload["state_topic"] = sensor["state_topic"]
            for k in ("min", "max", "step", "initial", "mode", "unit_of_measurement"):
                if k in sensor:
                    payload[k] = sensor[k]
        elif sensor["type"] == "camera":
            payload["topic"] = sensor["topic"]
            payload["image_encoding"] = sensor.get("image_encoding", "b64")
        else:
            base_topic = f"{self.config.MQTT_TOPIC_PREFIX}/state"
            payload["state_topic"] = base_topic
            payload["value_template"] = sensor["value_template"]

        # Optional fields
        for field in ["device_class", "unit_of_measurement", "icon"]:
            if field in sensor:
                payload[field] = sensor[field]

        return payload

    def publish_discovery(self):
        """Home Assistant Discovery 메시지 발행"""
        if not self.client or not self.connected:
            return

        try:
            discovery_prefix = self.config.HA_DISCOVERY_PREFIX

            for sensor in self._sensor_definitions:
                payload = self._build_discovery_payload(sensor)
                topic = f"{discovery_prefix}/{sensor['type']}/gshare/{sensor['id']}/config"
                self.client.publish(topic, json.dumps(payload), retain=True)

            logging.info("Home Assistant Discovery 메시지 발행 완료")

        except Exception as e:
            logging.error(f"Discovery 메시지 발행 중 오류: {e}")


    def publish_latest_image(self, image_bytes: bytes):
        """최근 스크린샷 이미지를 MQTT camera 토픽으로 발행"""
        if not self.client or not self.connected or not image_bytes:
            return

        try:
            image_topic = f"{self.config.MQTT_TOPIC_PREFIX}/camera/latest"
            payload = base64.b64encode(image_bytes).decode("ascii")
            self.client.publish(image_topic, payload, retain=True)
        except Exception as e:
            logging.error(f"MQTT 이미지 발행 중 오류: {e}")

    def disconnect(self):
        if self.client:
            # 종료 시 offline 메시지 전송
            try:
                if self.connected:
                    availability_topic = f"{self.config.MQTT_TOPIC_PREFIX}/status"
                    self.client.publish(availability_topic, "offline", retain=True)
            except Exception:
                pass

            self.client.loop_stop()
            self.client.disconnect()
            logging.info("MQTT 연결 종료")
