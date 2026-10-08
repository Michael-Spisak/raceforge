import CoreBluetooth
import Foundation
import TrackScoutKit

/// TrackScout as a Bluetooth LE peripheral for the paired laptop (spec 0007 scope 9, protocol rftx1).
///
/// The laptop (central) subscribes to the notify characteristic and writes requests to the other one; every
/// connection gets a fresh `PhoneTransferSession` that answers the laptop's challenge before offering anything.
/// Runs on the main queue; TrackScout must stay open during the transfer (no background mode in v1).
final class BluetoothSender: NSObject, CBPeripheralManagerDelegate, @unchecked Sendable {
    var onEvent: (PhoneTransferSession.Event) -> Void = { _ in }
    var onState: (String?) -> Void = { _ in }

    private var manager: CBPeripheralManager?
    private var tx: CBMutableCharacteristic?
    private var central: CBCentral?
    private var session: PhoneTransferSession?
    private var key = ""
    private var passes: [(offer: OfferedPass, url: URL)] = []
    private var incoming: AsyncStream<Data>.Continuation?

    /// Starts (or updates) advertising these passes to the paired laptop.
    func offer(_ passes: [(offer: OfferedPass, url: URL)], key: String) {
        self.passes = passes
        self.key = key
        if manager == nil {
            manager = CBPeripheralManager(delegate: self, queue: nil)
        } else if manager?.state == .poweredOn, tx == nil {
            addService()
        }
    }

    func stop() {
        manager?.stopAdvertising()
        manager?.removeAllServices()
        incoming?.finish()
        tx = nil
        session = nil
        central = nil
        passes = []
    }

    var isActive: Bool { !passes.isEmpty }

    private func addService() {
        guard let manager else { return }
        let tx = CBMutableCharacteristic(
            type: CBUUID(string: RFTX.txUUID), properties: [.notify], value: nil, permissions: [.readable])
        let rx = CBMutableCharacteristic(
            type: CBUUID(string: RFTX.rxUUID), properties: [.write], value: nil, permissions: [.writeable])
        let service = CBMutableService(type: CBUUID(string: RFTX.serviceUUID), primary: true)
        service.characteristics = [tx, rx]
        self.tx = tx
        manager.removeAllServices()
        manager.add(service)
    }

    // MARK: CBPeripheralManagerDelegate (main queue)

    func peripheralManagerDidUpdateState(_ peripheral: CBPeripheralManager) {
        switch peripheral.state {
        case .poweredOn:
            onState(nil)
            if !passes.isEmpty { addService() }
        case .unauthorized: onState(String(localized: "Bluetooth access is off for TrackScout (Settings → TrackScout)."))
        case .poweredOff: onState(String(localized: "Turn on Bluetooth to send to the laptop."))
        default: break
        }
    }

    func peripheralManager(_ peripheral: CBPeripheralManager, didAdd service: CBService, error: Error?) {
        guard error == nil else {
            onState(error?.localizedDescription)
            return
        }
        peripheral.startAdvertising([
            CBAdvertisementDataServiceUUIDsKey: [CBUUID(string: RFTX.serviceUUID)],
            CBAdvertisementDataLocalNameKey: "TrackScout",
        ])
    }

    func peripheralManager(_ peripheral: CBPeripheralManager, central: CBCentral, didSubscribeTo characteristic: CBCharacteristic) {
        self.central = central
        incoming?.finish()
        let (stream, continuation) = AsyncStream<Data>.makeStream()
        incoming = continuation
        let session = PhoneTransferSession(
            key: key, passes: passes, maxPayload: max(20, central.maximumUpdateValueLength - 1),
            send: { [weak self] data in
                guard let self else { throw CancellationError() }
                try await self.notify(data, to: central)
            },
            events: { [weak self] event in DispatchQueue.main.async { self?.onEvent(event) } })
        self.session = session
        // One consumer keeps the laptop's writes in order.
        Task {
            do {
                try await session.start()
                for await value in stream { try await session.receive(value) }
            } catch {
                DispatchQueue.main.async { [weak self] in self?.onState(error.localizedDescription) }
            }
        }
    }

    func peripheralManager(
        _ peripheral: CBPeripheralManager, central: CBCentral, didUnsubscribeFrom characteristic: CBCharacteristic
    ) {
        incoming?.finish()
        session = nil
        if self.central === central { self.central = nil }
    }

    func peripheralManager(_ peripheral: CBPeripheralManager, didReceiveWrite requests: [CBATTRequest]) {
        for r in requests where r.characteristic.uuid == CBUUID(string: RFTX.rxUUID) {
            if let value = r.value { incoming?.yield(value) }
        }
        if let first = requests.first { peripheral.respond(to: first, withResult: .success) }
    }

    /// Sends one GATT notification to the session's own central, waiting while the notification queue is
    /// full. Throws once that central is gone or replaced, which ends the session (no sends into the void,
    /// no chunks into a newer connection).
    private func notify(_ data: Data, to target: CBCentral) async throws {
        while true {
            let sent: Bool? = await MainActor.run {
                guard let manager, let tx, let central, central === target else { return nil }
                return manager.updateValue(data, for: tx, onSubscribedCentrals: [central])
            }
            guard let sent else { throw CancellationError() }
            if sent { return }
            // The notify queue is full: retry shortly (peripheralManagerIsReady would also signal this).
            try? await Task.sleep(nanoseconds: 15_000_000)
        }
    }
}
