from pydantic import BaseModel
from typing import Optional

class ProcessorBase(BaseModel):
    server: str
    ipv4: str
    system: str
    serial: str
    mac: str
    claimed: str
    sw_version: str
    status:str

    class Config:
        from_attributes = True

class ProcessorOut(ProcessorBase):
    id: int
    server: str


class ProcessorListAllOut(BaseModel):
    id: int
    ipv4: Optional[str] = None
    system: Optional[str] = None
    serial: Optional[str] = None
    handshake_status: Optional[bool] = None

    class Config:
        from_attributes = True


# Used by /processor_discovery/* only (does not change existing Processor* models).
class ProcessorCreate(BaseModel):
    ipv4: str
    mac: Optional[str] = None
    serial: Optional[str] = None
    system: Optional[str] = None


class IpScanRequest(BaseModel):
    ips: Optional[list[str]] = None
    raw: Optional[str] = None
    ports: Optional[list[int]] = None
    timeout: float = 2.0


class IpScanResult(BaseModel):
    ip: Optional[str] = None
    reachable: bool = False
    leap_8081: bool = False
    lap_8083: bool = False
    latency_ms: Optional[float] = None
    error: Optional[str] = None
    processor_id: Optional[int] = None
    serial: Optional[str] = None
    handshake_status: Optional[bool] = None
