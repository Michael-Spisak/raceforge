//! RaceForge car runtime core (spec 0005).
//!
//! - [`hw`]: hardware boundary of the loop (latest-value sensor store, actuator output) + mocks.
//! - [`safety`]: speed limit and teleop dead-man, enforced regardless of controller output.
//! - [`link`]: connection to the Python controller host process (ADR-0014) with per-step deadline.
//! - [`runtime`]: the fixed-rate control loop, fault latching and the supervisor watchdog.

pub mod hw;
pub mod link;
pub mod runtime;
pub mod safety;
