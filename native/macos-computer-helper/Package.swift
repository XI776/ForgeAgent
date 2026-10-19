// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "ForgeAgentComputerHelper",
    platforms: [.macOS(.v13)],
    products: [
        .library(name: "ForgeAgentComputerCore", targets: ["ForgeAgentComputerCore"]),
        .executable(name: "forgeagent-computer-helper", targets: ["ForgeAgentComputerHelper"]),
    ],
    targets: [
        .target(name: "ForgeAgentComputerCore"),
        .executableTarget(name: "ForgeAgentComputerHelper", dependencies: ["ForgeAgentComputerCore"]),
        .testTarget(name: "ForgeAgentComputerCoreTests", dependencies: ["ForgeAgentComputerCore"]),
    ]
)
