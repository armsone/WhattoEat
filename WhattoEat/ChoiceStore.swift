import Foundation

/// 확정한 메뉴+음식점 선택을 이 기기(UserDefaults)에만 기록한다. 서버 전송 없음.
final class ChoiceStore: ObservableObject {
    @Published private(set) var records: [ChoiceRecord] = []
    @Published private(set) var favorites: [FavoriteRecord] = []
    @Published private(set) var regionUsages: [RegionUsage] = []

    private let storageKey = "choiceRecords.v1"
    private let favoritesStorageKey = "favoriteRecords.v1"
    private let regionUsageStorageKey = "regionUsage.v1"
    private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        load()
    }

    func record(menu: String, restaurantName: String, regionName: String, imageName: String,
                imageURL: String? = nil, category: String? = nil,
                photoKind: String? = nil, photoProvider: String? = nil,
                photoSourceURL: String? = nil, photoAttribution: String? = nil,
                photoCreator: String? = nil, photoCreatorURL: String? = nil,
                photoLicense: String? = nil, photoLicenseURL: String? = nil,
                photoTitle: String? = nil) {
        records.append(ChoiceRecord(menu: menu,
                                    restaurantName: restaurantName,
                                    date: Date(),
                                    regionName: regionName,
                                    imageName: imageName,
                                    imageURL: imageURL,
                                    category: category,
                                    photoKind: photoKind,
                                    photoProvider: photoProvider,
                                    photoSourceURL: photoSourceURL,
                                    photoAttribution: photoAttribution,
                                    photoCreator: photoCreator,
                                    photoCreatorURL: photoCreatorURL,
                                    photoLicense: photoLicense,
                                    photoLicenseURL: photoLicenseURL,
                                    photoTitle: photoTitle))
        save()
    }

    func isFavorite(_ restaurantID: String) -> Bool {
        favorites.contains { $0.id == restaurantID }
    }

    func toggleFavorite(_ restaurant: Restaurant, regionName: String, imageName: String) {
        if isFavorite(restaurant.id) {
            removeFavorite(restaurant.id)
        } else {
            favorites.append(FavoriteRecord(id: restaurant.id,
                                             name: restaurant.name,
                                             category: restaurant.category,
                                             regionName: regionName,
                                             imageName: imageName,
                                             imageURL: restaurant.photoURL,
                                             photoKind: restaurant.photoKind,
                                             photoProvider: restaurant.photoProvider,
                                             photoSourceURL: restaurant.photoSourceURL,
                                             photoAttribution: restaurant.photoAttribution,
                                             photoCreator: restaurant.photoCreator,
                                             photoCreatorURL: restaurant.photoCreatorURL,
                                             photoLicense: restaurant.photoLicense,
                                             photoLicenseURL: restaurant.photoLicenseURL,
                                             photoTitle: restaurant.photoTitle,
                                             date: Date()))
        }
        saveFavorites()
    }

    /// 찜 목록에서도 추천 화면과 같은 단일 토글 경로를 사용한다.
    func toggleFavorite(_ favorite: FavoriteRecord) {
        guard isFavorite(favorite.id) else { return }
        removeFavorite(favorite.id)
        saveFavorites()
    }

    func delete(_ record: ChoiceRecord) {
        guard let index = records.firstIndex(where: {
            $0.date == record.date &&
            $0.menu == record.menu &&
            $0.restaurantName == record.restaurantName
        }) else { return }
        records.remove(at: index)
        save()
    }

    func delete(_ favorite: FavoriteRecord) {
        removeFavorite(favorite.id)
        saveFavorites()
    }

    private func removeFavorite(_ id: String) {
        favorites.removeAll { $0.id == id }
    }

    /// 실제 추천에 사용한 지역만 누적한다. 횟수 내림차순, 동률은 최근 사용순이다.
    func recordRegionSelection(_ rawName: String, latitude: Double, longitude: Double) {
        let name = rawName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty, name != "현재 위치", name != "지정 지역" else { return }
        if let index = regionUsages.firstIndex(where: { $0.name == name }) {
            regionUsages[index].count += 1
            regionUsages[index].lastUsedAt = Date()
        } else {
            regionUsages.append(RegionUsage(name: name, count: 1, lastUsedAt: Date(),
                                            latitude: latitude, longitude: longitude))
        }
        saveRegionUsages()
    }

    var frequentRegions: [String] {
        regionUsages
            .sorted {
                $0.count != $1.count ? $0.count > $1.count : $0.lastUsedAt > $1.lastUsedAt
            }
            .prefix(3)
            .map(\.name)
    }

    /// 메뉴별 선택 횟수 순위(이 기기 기준).
    var ranking: [(menu: String, count: Int)] {
        var counts: [String: Int] = [:]
        for record in records {
            counts[record.menu, default: 0] += 1
        }
        return counts
            .map { (menu: $0.key, count: $0.value) }
            .sorted { $0.count != $1.count ? $0.count > $1.count : $0.menu < $1.menu }
    }

    private func load() {
        if let data = defaults.data(forKey: storageKey),
           let decoded = try? JSONDecoder().decode([ChoiceRecord].self, from: data) {
            records = decoded
        }
        if let favoriteData = defaults.data(forKey: favoritesStorageKey),
           let decodedFavorites = try? JSONDecoder().decode([FavoriteRecord].self, from: favoriteData) {
            favorites = decodedFavorites
        }
        if let regionData = defaults.data(forKey: regionUsageStorageKey),
           let decodedRegions = try? JSONDecoder().decode([RegionUsage].self, from: regionData) {
            regionUsages = decodedRegions
        }
    }

    private func save() {
        // Foursquare CDN URL은 세션 안에서만 쓰고 기기 저장소에 남기지 않는다.
        let persistableRecords = records.map { record in
            guard record.photoProvider?.lowercased() == "foursquare" else { return record }
            return ChoiceRecord(menu: record.menu,
                                restaurantName: record.restaurantName,
                                date: record.date,
                                regionName: record.regionName,
                                imageName: record.imageName,
                                imageURL: nil,
                                category: record.category,
                                photoKind: nil,
                                photoProvider: nil,
                                photoSourceURL: nil,
                                photoAttribution: nil,
                                photoCreator: nil,
                                photoCreatorURL: nil,
                                photoLicense: nil,
                                photoLicenseURL: nil,
                                photoTitle: nil)
        }
        guard let data = try? JSONEncoder().encode(persistableRecords) else { return }
        defaults.set(data, forKey: storageKey)
    }

    private func saveFavorites() {
        let persistableFavorites = favorites.map { favorite in
            guard favorite.photoProvider?.lowercased() == "foursquare" else { return favorite }
            return FavoriteRecord(id: favorite.id,
                                  name: favorite.name,
                                  category: favorite.category,
                                  regionName: favorite.regionName,
                                  imageName: favorite.imageName,
                                  imageURL: nil,
                                  photoKind: nil,
                                  photoProvider: nil,
                                  photoSourceURL: nil,
                                  photoAttribution: nil,
                                  photoCreator: nil,
                                  photoCreatorURL: nil,
                                  photoLicense: nil,
                                  photoLicenseURL: nil,
                                  photoTitle: nil,
                                  date: favorite.date)
        }
        guard let data = try? JSONEncoder().encode(persistableFavorites) else { return }
        defaults.set(data, forKey: favoritesStorageKey)
    }

    private func saveRegionUsages() {
        guard let data = try? JSONEncoder().encode(regionUsages) else { return }
        defaults.set(data, forKey: regionUsageStorageKey)
    }
}
