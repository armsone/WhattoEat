import SwiftUI

@main
struct WhattoEatApp: App {
    var body: some Scene {
#if targetEnvironment(macCatalyst)
        WindowGroup {
            GeometryReader { geometry in
                ContentView()
                    .frame(width: geometry.size.width / 1.5,
                           height: geometry.size.height / 1.5,
                           alignment: .topLeading)
                    .scaleEffect(1.5, anchor: .topLeading)
            }
            .task { MacDirectUpdateManager.shared.start() }
        }
        .defaultSize(width: 750, height: 1200)
#else
        WindowGroup {
            ContentView()
        }
#endif
    }
}
