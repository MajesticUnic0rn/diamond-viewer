/**
 * diamond-material.js
 *
 * BVH-based diamond refraction material for Three.js.
 * Closely follows @react-three/drei MeshRefractionMaterial,
 * adapted for vanilla Three.js (no React dependency).
 * Uses three-mesh-bvh for accurate internal ray tracing.
 */

import {
    MeshBVH,
    MeshBVHUniformStruct,
    SAH,
    shaderStructs,
    shaderIntersectFunction,
} from 'three-mesh-bvh';

export function createDiamondMaterial(THREE, geometry, envMap, opts = {}) {
    const {
        bounces = 3,
        ior = 2.42,
        fresnel = 1.0,
        aberrationStrength = 0.01,
        color = new THREE.Color('white'),
        fastChroma = true,
    } = opts;

    // Convert to non-indexed geometry (matching drei's approach)
    const geom = geometry.clone().toNonIndexed();

    // Ensure indexed geometry for MeshBVHUniformStruct GPU textures
    if (!geom.index) {
        const count = geom.attributes.position.count;
        const idx = new Uint32Array(count);
        for (let i = 0; i < count; i++) idx[i] = i;
        geom.setIndex(new THREE.BufferAttribute(idx, 1));
    }

    // Build BVH for GPU ray tracing
    const bvh = new MeshBVH(geom, { strategy: SAH });
    const bvhStruct = new MeshBVHUniformStruct();
    bvhStruct.updateFrom(bvh);

    // Shader defines for compile-time branching
    const defines = {};
    if (aberrationStrength > 0) {
        defines.CHROMATIC_ABERRATIONS = '';
        if (fastChroma) defines.FAST_CHROMA = '';
    }

    const material = new THREE.ShaderMaterial({
        defines,
        toneMapped: false,
        uniforms: {
            envMap: { value: envMap },
            bvh: { value: bvhStruct },
            bounces: { value: bounces },
            ior: { value: ior },
            fresnel: { value: fresnel },
            aberrationStrength: { value: aberrationStrength },
            color: { value: color },
        },
        vertexShader: /* glsl */ `
            varying vec3 vWorldPosition;
            varying vec3 vNormal;
            varying mat4 vModelMatrixInverse;

            void main() {
                vec4 transformedPosition = vec4(position, 1.0);
                vec4 transformedNormal = vec4(normal, 0.0);

                vModelMatrixInverse = inverse(modelMatrix);
                vWorldPosition = (modelMatrix * transformedPosition).xyz;
                vNormal = normalize((modelMatrix * transformedNormal).xyz);
                gl_Position = projectionMatrix * viewMatrix * modelMatrix * transformedPosition;
            }
        `,
        fragmentShader: /* glsl */ `
            precision highp isampler2D;
            precision highp usampler2D;

            varying vec3 vWorldPosition;
            varying vec3 vNormal;
            varying mat4 vModelMatrixInverse;

            uniform samplerCube envMap;
            uniform float bounces;
            ${shaderStructs}
            ${shaderIntersectFunction}
            uniform BVH bvh;
            uniform float ior;
            uniform float fresnel;
            uniform mat4 modelMatrix;
            uniform float aberrationStrength;
            uniform vec3 color;

            float fresnelFunc(vec3 viewDirection, vec3 worldNormal) {
                return pow(1.0 + dot(viewDirection, worldNormal), 10.0);
            }

            vec3 totalInternalReflection(vec3 ro, vec3 rd, vec3 normal, float iorVal, mat4 modelMatrixInverse) {
                vec3 rayOrigin = ro;
                vec3 rayDirection = rd;

                // Initial refraction into the diamond (world space)
                rayDirection = refract(rayDirection, normal, 1.0 / iorVal);
                rayOrigin = vWorldPosition + rayDirection * 0.001;

                // Transform to object space for BVH tracing
                rayOrigin = (modelMatrixInverse * vec4(rayOrigin, 1.0)).xyz;
                rayDirection = normalize((modelMatrixInverse * vec4(rayDirection, 0.0)).xyz);

                // Bounce inside diamond
                for (float i = 0.0; i < bounces; i++) {
                    uvec4 faceIndices = uvec4(0u);
                    vec3 faceNormal = vec3(0.0, 0.0, 1.0);
                    vec3 barycoord = vec3(0.0);
                    float side = 1.0;
                    float dist = 0.0;

                    bvhIntersectFirstHit(bvh, rayOrigin, rayDirection, faceIndices, faceNormal, barycoord, side, dist);

                    vec3 hitPos = rayOrigin + rayDirection * max(dist - 0.001, 0.0);

                    // Try to refract out of the diamond
                    vec3 tempDir = refract(rayDirection, faceNormal, iorVal);
                    if (length(tempDir) != 0.0) {
                        rayDirection = tempDir;
                        break;
                    }

                    // Total internal reflection — keep bouncing
                    rayDirection = reflect(rayDirection, faceNormal);
                    rayOrigin = hitPos + rayDirection * 0.01;
                }

                // Transform exit ray back to world space
                rayDirection = normalize((modelMatrix * vec4(rayDirection, 0.0)).xyz);
                return rayDirection;
            }

            void main() {
                vec3 normal = vNormal;
                vec3 rayOrigin = cameraPosition;
                vec3 rayDirection = normalize(vWorldPosition - cameraPosition);

                vec3 col = color;

                #ifdef CHROMATIC_ABERRATIONS
                    vec3 rayDirectionG = totalInternalReflection(rayOrigin, rayDirection, normal, max(ior, 1.0), vModelMatrixInverse);
                    #ifdef FAST_CHROMA
                        vec3 rayDirectionR = normalize(rayDirectionG + 1.0 * vec3(aberrationStrength / 2.0));
                        vec3 rayDirectionB = normalize(rayDirectionG - 1.0 * vec3(aberrationStrength / 2.0));
                    #else
                        vec3 rayDirectionR = totalInternalReflection(rayOrigin, rayDirection, normal, max(ior * (1.0 - aberrationStrength), 1.0), vModelMatrixInverse);
                        vec3 rayDirectionB = totalInternalReflection(rayOrigin, rayDirection, normal, max(ior * (1.0 + aberrationStrength), 1.0), vModelMatrixInverse);
                    #endif
                    float finalColorR = texture(envMap, rayDirectionR).r;
                    float finalColorG = texture(envMap, rayDirectionG).g;
                    float finalColorB = texture(envMap, rayDirectionB).b;
                    col *= vec3(finalColorR, finalColorG, finalColorB);
                #else
                    rayDirection = totalInternalReflection(rayOrigin, rayDirection, normal, max(ior, 1.0), vModelMatrixInverse);
                    col *= texture(envMap, rayDirection).rgb;
                #endif

                // Fresnel surface reflection
                vec3 viewDirection = normalize(vWorldPosition - cameraPosition);
                float nFresnel = fresnelFunc(viewDirection, normal) * fresnel;
                gl_FragColor = vec4(mix(col, vec3(1.0), nFresnel), 1.0);
            }
        `,
    });

    return { material, bvhStruct, bvh };
}
