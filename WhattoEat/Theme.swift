import SwiftUI

enum AppTypography {
    static let screenTitle = Font.system(size: 20, weight: .bold)
    static let sectionTitle = Font.system(size: 17, weight: .semibold)
    static let rowTitle = Font.system(size: 15, weight: .medium)
    static let supporting = Font.system(size: 13, weight: .regular)
}

// 승인된 단일 밝은 테마: 페일 민트 + 웜 아이보리 에나멜이 지배하고,
// 카라멜 가죽은 히어로/카드, 크롬은 가는 림, #E41E25는 아주 작은 포인트에만 쓴다.
// 다크 테마는 제공하지 않는다.
extension Color {
    /// 첨부 시안의 따뜻한 아이보리 화면 바탕.
    static let mintBase = Color(red: 251 / 255, green: 248 / 255, blue: 242 / 255)
    /// 카드·아이콘 웰 표면. 웜 아이보리 에나멜.
    static let ivory = Color(red: 1.0, green: 0.992, blue: 0.968)
    /// 히어로/주요 버튼. 카라멜 가죽.
    static let caramel = Color(red: 0.62, green: 0.34, blue: 0.14)
    /// 카라멜 위 텍스트 대비용 짙은 카라멜.
    static let caramelDeep = Color(red: 0.48, green: 0.31, blue: 0.17)
    /// 가는 크롬 림/하이라이트 전용. 넓은 면에는 쓰지 않는다.
    static let chrome = Color(red: 0.78, green: 0.75, blue: 0.68)
    /// 본문 텍스트. 순수 검정 대신 차콜.
    static let charcoalText = Color(red: 0.18, green: 0.20, blue: 0.22)
    /// 보조 텍스트.
    static let charcoalSoft = Color(red: 0.18, green: 0.20, blue: 0.22).opacity(0.70)
    /// 포인트 #E41E25. 작은 강조(순위 숫자, 배지)에만 소량 사용. 넓은 빨간 버튼 금지.
    static let accentRed = Color(red: 228 / 255, green: 30 / 255, blue: 37 / 255)
    static let selectionMint = Color(red: 0.86, green: 0.95, blue: 0.90)
    static let mintInk = Color(red: 0.12, green: 0.34, blue: 0.26)
    static let leatherLight = Color(red: 0.70, green: 0.42, blue: 0.19)
    static let canvasLine = Color(red: 0.84, green: 0.81, blue: 0.74)
}

/// 주요 액션 버튼: 카라멜 가죽 + 가는 크롬 상단 하이라이트.
struct PrimaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.headline)
            .foregroundStyle(Color.ivory)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 14)
            .background(Color.caramel.opacity(configuration.isPressed ? 0.75 : 1))
            .clipShape(RoundedRectangle(cornerRadius: 14))
            .overlay(
                RoundedRectangle(cornerRadius: 14)
                    .strokeBorder(Color.chrome.opacity(0.9), lineWidth: 1)
            )
    }
}

/// 보조 버튼: 아이보리 에나멜 + 차콜 텍스트 + 크롬 림.
struct SecondaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.headline)
            .foregroundStyle(Color.charcoalText)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 14)
            .background(Color.ivory.opacity(configuration.isPressed ? 0.7 : 1))
            .clipShape(RoundedRectangle(cornerRadius: 14))
            .overlay(
                RoundedRectangle(cornerRadius: 14)
                    .strokeBorder(Color.chrome, lineWidth: 1)
            )
    }
}

/// 화면·섹션 제목 앞에 붙는 일관된 에나멜/크롬 아이콘 웰.
struct IconWell: View {
    let systemName: String
    var diameter: CGFloat = 28

    var body: some View {
        Image(systemName: systemName)
            .font(.system(size: diameter * 0.45, weight: .semibold))
            .foregroundStyle(Color.charcoalText)
            .frame(width: diameter, height: diameter)
            .background(
                Circle().fill(
                    LinearGradient(colors: [.white, Color.ivory], startPoint: .top, endPoint: .bottom)
                )
            )
            .overlay(Circle().strokeBorder(Color.chrome.opacity(0.72), lineWidth: 1))
            .shadow(color: Color.charcoalText.opacity(0.13), radius: 2, y: 2)
            .accessibilityHidden(true)
    }
}

/// 섹션 제목: 아이콘 웰 + 텍스트.
struct SectionTitle: View {
    let icon: String
    let title: String

    var body: some View {
        HStack(spacing: 8) {
            IconWell(systemName: icon, diameter: 24)
            Text(title)
                .font(AppTypography.sectionTitle)
                .foregroundStyle(Color.charcoalText)
        }
        .textCase(nil)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isHeader)
    }
}

/// 화면 제목(내비게이션 principal)용.
struct ScreenTitle: View {
    let icon: String
    let title: String

    var body: some View {
        HStack(spacing: 8) {
            IconWell(systemName: icon, diameter: 26)
            Text(title)
                .font(AppTypography.screenTitle)
                .foregroundStyle(Color.charcoalText)
        }
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isHeader)
    }
}

extension View {
    /// 아이보리 에나멜 카드 + 크롬 헤어라인.
    func enamelCard() -> some View {
        background(Color.ivory)
            .clipShape(RoundedRectangle(cornerRadius: 14))
            .overlay(
                RoundedRectangle(cornerRadius: 14)
                    .strokeBorder(Color.chrome, lineWidth: 1)
            )
    }

    func referenceCard(cornerRadius: CGFloat = 18) -> some View {
        background(Color.ivory)
            .clipShape(RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .strokeBorder(Color.canvasLine.opacity(0.8), lineWidth: 1)
            )
            .shadow(color: Color.caramelDeep.opacity(0.08), radius: 5, y: 2)
    }
}
