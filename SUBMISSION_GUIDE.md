# MERIDIAN submission guide

## Repository entry points

- [Project overview](README.md)
- [Submission packet](submission/README.md)
- [Technical documentation](docs/)
- [Demonstration screenshots](assets/screenshots/README.md)

## Android release

From `mobile/`, run:

```powershell
flutter pub get
flutter analyze
flutter test
flutter build apk --release
```

The APK is written to
`mobile/build/app/outputs/flutter-apk/app-release.apk`. A team-owned Android
release keystore is required for public distribution; the repository does not
store signing keys.

## iOS release

The iOS Runner project is tracked in `mobile/ios/`. iOS archives require macOS
and Xcode. The GitHub Actions release workflow compiles an unsigned iOS release
on macOS for verification. Distribution to devices or TestFlight additionally
requires an Apple Developer signing identity and provisioning profile.

## Before final submission

1. Confirm the GitHub Actions release jobs are green.
2. Attach the Android release APK where permitted by SIH rules.
3. Add final application screenshots under `assets/screenshots/`.
4. Use the evidence and limitations stated in `submission/`; do not represent
   offline replay metrics as field-drive results.
