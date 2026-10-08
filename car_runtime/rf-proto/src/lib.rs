//! Wire protocols of the RaceForge car runtime (spec 0005, ADR-0014).
//!
//! - [`ev3`]: binary frames between the board and the EV3 (UDP over the USB-Ethernet gadget).
//! - [`ld06`]: streaming parser for LD06/LD19 2D LiDAR packets.
//! - [`ipc`]: JSON messages between the Rust core and the Python controller host.

pub mod cobs;
pub mod crc;
pub mod ev3;
pub mod ipc;
pub mod ld06;
