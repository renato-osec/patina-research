pub struct Header {
    pub flags: u8,
    pub id: u32,
    pub len: u16,
}

pub struct Point {
    pub x: f64,
    pub y: f64,
}

pub struct Message {
    pub header: Header,
    pub seq: u64,
    pub origin: *const Point,
    pub payload_len: u32,
    pub urgent: bool,
}
