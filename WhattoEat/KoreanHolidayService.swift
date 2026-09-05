import Foundation

// Reused from HanClip: Korea holiday feed, JSON formats, and local cache.
enum KoreanHolidayService {
    private static let cacheKey = "whattoEatKoreanHolidayJSON"
    private static let endpoint =
        "https://holidays.hyunbin.page/basic.json"

    static func cachedHolidayNames(for year: Int, calendar: Calendar)
        -> [Date: String]
    {
        holidayNames(
            from: cachedHolidayMap(),
            year: year,
            calendar: calendar
        )
    }

    static func refreshedHolidayNames(for year: Int, calendar: Calendar)
        async -> [Date: String]
    {
        do {
            let holidayMap = try await fetchHolidayMap()
            cache(holidayMap)
            return holidayNames(
                from: cachedHolidayMap(),
                year: year,
                calendar: calendar
            )
        } catch {
            return cachedHolidayNames(for: year, calendar: calendar)
        }
    }

    private static func fetchHolidayMap() async throws
        -> [String: [String]]
    {
        guard let url = URL(string: endpoint) else { return [:] }
        let request = URLRequest(url: url, timeoutInterval: 8)
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse,
              (200..<300).contains(httpResponse.statusCode)
        else { throw URLError(.badServerResponse) }

        let map = try decodeHolidayMap(from: data)
        guard !map.isEmpty else { throw URLError(.cannotParseResponse) }
        return map
    }

    private static func cachedHolidayMap() -> [String: [String]] {
        let bundled = Bundle.main.url(forResource: "KoreanHolidays", withExtension: "json")
            .flatMap { try? Data(contentsOf: $0) }
            .flatMap { try? decodeHolidayMap(from: $0) } ?? [:]
        guard let data = UserDefaults.standard.data(forKey: cacheKey),
              let cached = try? decodeHolidayMap(from: data) else { return bundled }
        // A refreshed year replaces that year's bundled dates; retain other years offline.
        let cachedYears = Set(cached.keys.map { String($0.prefix(4)) })
        return bundled.filter { !cachedYears.contains(String($0.key.prefix(4))) }
            .merging(cached) { _, fresh in fresh }
    }

    private static func cache(_ holidayMap: [String: [String]]) {
        guard let data = try? JSONEncoder().encode(holidayMap) else { return }
        UserDefaults.standard.set(data, forKey: cacheKey)
    }

    private static func decodeHolidayMap(from data: Data) throws
        -> [String: [String]]
    {
        if let years = try? JSONDecoder()
            .decode([String: [String: [String]]].self, from: data) {
            return years.values.reduce(into: [String: [String]]()) {
                result,
                holidays in
                holidays.forEach { date, names in
                    result[date] = names
                }
            }
        }

        if let dates = try? JSONDecoder().decode([String: [String]].self, from: data) {
            return dates
        }

        return try JSONDecoder()
            .decode([String: String].self, from: data)
            .mapValues { [$0] }
    }

    private static func holidayNames(
        from holidayMap: [String: [String]],
        year: Int,
        calendar: Calendar
    ) -> [Date: String] {
        let prefix = "\(year)-"
        return holidayMap.reduce(into: [Date: String]()) { result, item in
            let key = item.key
            guard key.hasPrefix(prefix) else { return }
            let parts = key.split(separator: "-").compactMap {
                Int(String($0))
            }
            guard parts.count == 3 else { return }
            guard let date = calendar.date(
                from: DateComponents(
                    year: parts[0],
                    month: parts[1],
                    day: parts[2]
                )
            ) else { return }
            guard let name = item.value.first else { return }
            result[calendar.startOfDay(for: date)] = name
        }
    }
}
