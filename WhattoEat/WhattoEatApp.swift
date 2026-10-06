import SwiftUI
import UserNotifications

@MainActor
final class LunchNotificationRoute: ObservableObject {
    static let shared = LunchNotificationRoute()
    @Published var recommendationPending = false
}

final class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        return true
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                didReceive response: UNNotificationResponse) async {
        guard response.actionIdentifier == UNNotificationDefaultActionIdentifier else { return }
        let identifier = response.notification.request.identifier
        await MainActor.run {
            guard identifier == LunchReminder.identifier ||
                    identifier.hasPrefix(LunchReminder.identifier + ".") else { return }
            LunchNotificationRoute.shared.recommendationPending = true
        }
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                willPresent notification: UNNotification) async -> UNNotificationPresentationOptions {
        let identifier = notification.request.identifier
        return await MainActor.run {
            guard identifier == LunchReminder.identifier ||
                    identifier.hasPrefix(LunchReminder.identifier + ".") else { return [] }
            return [.banner, .sound, .list]
        }
    }
}

@main
struct WhattoEatApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

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
